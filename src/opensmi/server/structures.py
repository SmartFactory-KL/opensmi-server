# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Structure DataTypes defined as dataclasses, for companion specifications without a nodeset.

A dataclass is the definition. `Server.ensure_structure` creates the DataType, its Default Binary encoding and
its ``DataTypeDefinition``, and teaches asyncua the class, so instances can be stored in a variable, written to
it and read from it like any other value::

    @dataclass
    class Point:
        x: float = 0.0
        y: float = 0.0


    data_type = await server.ensure_structure(Point)
    position = UaVariable(Point(), name="Position", ua_data_type=data_type)

A `UaVariable` whose initial value is such a dataclass instance, or a list of them, does this itself, in
namespace 1 like enums. For an empty list, pass the class as ``variable_type``. If the DataType already exists
(from an imported nodeset, or created elsewhere), `Server.register_structure` binds a class to it without
creating anything. A client needs no class of its own: ``Client.load_data_type_definitions()`` builds one from
the ``DataTypeDefinition``.

**Field types.** Annotate fields with plain Python types, which stand for ``float``: Double, ``int``: Int64
(like a plain int in a `UaVariable`), ``str``: String, ``bool``: Boolean and ``bytes``: ByteString. Where a
field needs another OPC UA type, use the classes of ``asyncua.ua``: ``SByte``, ``Byte``, ``Int16``, ``UInt16``,
``Int32``, ``UInt32``, ``UInt64``, ``Float`` and the like. A field may also be another dataclass (created
first, automatically by ``ensure_structure``) or a ``list`` of any of these for a one-dimensional array.
Anything else is refused with a `TypeError` that names the field. Because asyncua encodes by the annotations of
the class and knows only its own types, registering a class replaces its plain annotations by the ``ua``
classes, in place. This changes the class for everybody who uses it.

**Names.** The NodeIds are strings derived from the class name (``<name>`` and ``<name>.DefaultBinary``), so
they do not depend on the order of registration. A different class under a name that is taken in the same
namespace is refused with `TypeConflictError` before any node is created, even if its fields are the same: asyncua
decodes each encoding into exactly one class, so a second class would take the values of the first.

**One DataType per class.** A class has one DataType per server, because asyncua encodes a class with exactly one
encoding. Asking again for a class that has one, or nesting it in another, gives back that DataType, the bound
one included, and creates nothing. Asking for it in another namespace, binding it to another DataType, or binding
another class to its DataType, is refused with `TypeConflictError`.

**Writes.** asyncua stores any ExtensionObject in a variable regardless of the variable's DataType, so a structure
variable checks every write itself: a value that is not an instance of its class (or, for a list, not a list of
them) is refused with ``BadTypeMismatch``. A ``write_check_callback`` of the variable runs after this check, never
instead of it, so it always gets a value of the right class. ``None`` passes the check if the variable is
``optional_ok``.

**Trust.** Beyond these checks, nothing is verified. A class bound with ``register_structure`` must match
the DataTypeDefinition of its DataType, a variable with an explicit ``ua_data_type`` needs its class registered
or bound with that DataType, and only the first element of a list decides the class of a variable. Get these
wrong and encoding fails; use them only if you know what you are doing.

**One process.** asyncua keeps its table of structure classes per process, keyed by NodeId. This is acceptable
here because one `Server` per process is the normal case; two Servers in one process must not give different
classes the same name in one namespace.

Everything is reached through `Server` and `UaVariable`; the contents of this module are private.
`TypeConflictError` lives in `opensmi.server.errors`.
"""

import asyncio
import dataclasses
import types
import typing
from enum import Enum
from typing import TYPE_CHECKING, Final, NamedTuple

from asyncua import ua
from asyncua.common.manage_nodes import create_encoding
from asyncua.ua.object_ids import ObjectIds
from asyncua.ua.status_codes import StatusCodes

from opensmi.server.errors import TypeConflictError

__all__: list[str] = []  # everything here is private; use Server and UaVariable

if TYPE_CHECKING:
    from asyncua.server.server import Server

_STRUCTURE: Final = ua.NodeId(ua.Int32(ObjectIds.Structure), ua.Int16(0))
_DATA_TYPE_ENCODING_TYPE: Final = ua.NodeId(ua.Int32(ObjectIds.DataTypeEncodingType), ua.Int16(0))
_HAS_TYPE_DEFINITION: Final = ua.NodeId(ua.Int32(ObjectIds.HasTypeDefinition), ua.Int16(0))
_ENCODING_SUFFIX: Final = ".DefaultBinary"
_ENCODING_NAME: Final = "Default Binary"

_SCALARS: Final[dict[object, ua.VariantType]] = {
    getattr(ua, name): ua.VariantType[name]
    for name in (
        "Boolean",
        "SByte",
        "Byte",
        "Int16",
        "UInt16",
        "Int32",
        "UInt32",
        "Int64",
        "UInt64",
        "Float",
        "Double",
        "String",
        "ByteString",
    )
}
"""The asyncua class of each supported built-in type, and the VariantType that names it."""

_PLAIN: Final[dict[object, type]] = {
    float: ua.Double,
    int: ua.Int64,  # what UaVariable and asyncua make of a plain int
    str: ua.String,
    bool: ua.Boolean,
    bytes: ua.ByteString,
}
"""Plain Python annotations and the ua class each stands for."""


class _Field(NamedTuple):
    name: str
    element: type
    """A `_SCALARS` class, or a dataclass for a nested structure."""
    array: bool


class _StructureRegistry:
    """What a `Server` knows about structures. Lives as long as the server and is never reset."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self.lock = asyncio.Lock()
        """Held while structures are created or bound."""
        self.by_class: dict[type, ua.NodeId] = {}
        """The one DataType of each class that has one, whether created here or bound to an existing one."""
        self.created: dict[tuple[int, str], type] = {}
        """The class of each structure created, by namespace index and name."""


def _fields(cls: type) -> list[_Field]:
    if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
        msg = f"{cls!r} is not a dataclass"
        raise TypeError(msg)
    hints = typing.get_type_hints(cls)
    return [_Field(each.name, *_element(cls, each.name, hints[each.name])) for each in dataclasses.fields(cls)]


def _use_ua_annotations(cls: type, fields: list[_Field]) -> None:
    # asyncua encodes by the annotations of the class, and it only knows its own types: float would not encode
    for each in fields:
        cls.__annotations__[each.name] = list[each.element] if each.array else each.element


def _element(cls: type, name: str, hint: object) -> tuple[type, bool]:
    array = typing.get_origin(hint) is list
    if array:
        (hint,) = typing.get_args(hint)
    if hint in _PLAIN:
        hint = _PLAIN[hint]
    if isinstance(hint, type) and (hint in _SCALARS or _is_user_dataclass(hint)):
        return hint, array
    where = f"field {name!r} of {cls.__name__}"
    if typing.get_origin(hint) in (typing.Union, types.UnionType):
        msg = f"{where} is annotated {hint!r}: Optional fields are not supported"
    else:
        msg = f"{where} is annotated {hint!r}, which is not a ua scalar, a dataclass or a list of one of them"
    raise TypeError(msg)


def _is_user_dataclass(hint: type) -> bool:
    # asyncua's own types (NodeId, Variant, ...) are dataclasses too, and are not structures to register
    return dataclasses.is_dataclass(hint) and not hint.__module__.startswith("asyncua")


def _structure_class(value: object, variable_type: object = None) -> type | None:
    """Return the dataclass of a value, or of the first element of a list, or ``None`` if it is not a structure.

    Only the first element decides, the others are not looked at. An empty list has no element, so its class is
    ``variable_type``, if that is a dataclass.
    """
    if isinstance(value, list):
        if value:
            value = value[0]
        elif isinstance(variable_type, type) and _is_user_dataclass(variable_type):
            return variable_type
    cls = type(value)
    return cls if _is_user_dataclass(cls) and not issubclass(cls, Enum) else None


def _collect(cls: type, found: dict[str, tuple[type, list[_Field]]], known: dict[type, ua.NodeId]) -> None:
    """Add the structures ``cls`` needs to ``found``, nested ones first and ``cls`` itself last.

    A nested class in ``known`` already has its DataType and is not collected.
    """
    seen = found.get(cls.__name__)
    if seen is not None:
        if seen[0] is not cls:
            raise _name_taken(cls.__name__, seen[0], cls)
        return
    fields = _fields(cls)
    for each in fields:
        if each.element not in _SCALARS and each.element not in known:
            _collect(each.element, found, known)
    found[cls.__name__] = (cls, fields)


def _name_taken(name: str, owner: type, cls: type, namespace_index: int | None = None) -> TypeConflictError:
    where = "" if namespace_index is None else f" in namespace {namespace_index}"
    msg = (
        f"structure {name!r}{where} is already defined by {owner.__module__}.{owner.__qualname__}, "
        f"so {cls.__module__}.{cls.__qualname__} cannot have it, even with the same fields"
    )
    return TypeConflictError(msg)


async def _ensure_structure(
    registry: _StructureRegistry, server: "Server", cls: type, namespace_index: int | None = None
) -> ua.NodeId:
    """Create the DataType of a dataclass on a server, and of the dataclasses it nests, unless they exist.

    This is `Server.ensure_structure`, see there.
    """
    async with registry.lock:
        if (known := registry.by_class.get(cls)) is not None:
            if namespace_index is not None and known.NamespaceIndex != namespace_index:
                msg = (
                    f"{cls.__name__} already has the DataType {known.to_string()}, "
                    f"so it cannot get one in namespace {namespace_index}"
                )
                raise TypeConflictError(msg)
            return known
        namespace_index = 1 if namespace_index is None else namespace_index
        found: dict[str, tuple[type, list[_Field]]] = {}
        _collect(cls, found, registry.by_class)
        for name, (each, _) in found.items():
            # a class in found has no DataType yet, so whoever has the name is a different class
            if (owner := registry.created.get((namespace_index, name))) is not None:
                raise _name_taken(name, owner, each, namespace_index)
        # only now that nothing was refused may the annotations of the classes change
        for each, fields in found.values():
            _use_ua_annotations(each, fields)
        for name, (each, fields) in found.items():
            await _create(server, namespace_index, name, fields, registry.by_class)
            registry.created[namespace_index, name] = each
            ua.register_extension_object(
                name, _node_id(name + _ENCODING_SUFFIX, namespace_index), each, _node_id(name, namespace_index)
            )
            registry.by_class[each] = _node_id(name, namespace_index)
        return registry.by_class[cls]


async def _register_structure(
    registry: _StructureRegistry, server: "Server", cls: type, ua_data_type: ua.NodeId
) -> None:
    """Bind a dataclass to a DataType that exists. This is `Server.register_structure`, see there."""
    fields = _fields(cls)
    encoding_id = None
    for reference in await server.get_node(ua_data_type).get_references(
        refs=ObjectIds.HasEncoding, direction=ua.BrowseDirection.Forward
    ):
        if (await server.get_node(reference.NodeId).read_browse_name()).Name == _ENCODING_NAME:
            encoding_id = reference.NodeId
            break
    if encoding_id is None:
        msg = f"the DataType {ua_data_type.to_string()} has no {_ENCODING_NAME!r} encoding"
        raise ValueError(msg)
    async with registry.lock:
        known = registry.by_class.get(cls)
        if known is not None and known != ua_data_type:
            msg = f"{cls.__name__} already has the DataType {known.to_string()}, not {ua_data_type.to_string()}"
            raise TypeConflictError(msg)
        for other, data_type in registry.by_class.items():
            if data_type == ua_data_type and other is not cls:
                msg = (
                    f"the DataType {ua_data_type.to_string()} already belongs to {other.__module__}."
                    f"{other.__qualname__}, so {cls.__module__}.{cls.__qualname__} cannot be bound to it"
                )
                raise TypeConflictError(msg)
        _use_ua_annotations(cls, fields)
        ua.register_extension_object(cls.__name__, encoding_id, cls, ua_data_type)
        registry.by_class[cls] = ua_data_type


def _node_id(name: str, namespace_index: int) -> ua.NodeId:
    return ua.StringNodeId(Identifier=ua.String(name), NamespaceIndex=ua.Int16(namespace_index))


async def _create(
    server: "Server", namespace_index: int, name: str, fields: list[_Field], known: dict[type, ua.NodeId]
) -> None:
    encoding_id = _node_id(name + _ENCODING_SUFFIX, namespace_index)
    definition = ua.StructureDefinition(
        DefaultEncodingId=encoding_id,
        BaseDataType=_STRUCTURE,
        # Plain structure: every field is present, so there are no optional fields and no encoding mask.
        Fields=[_ua_field(each, namespace_index, known) for each in fields],
    )
    node = await server.nodes.base_structure_type.add_data_type(
        _node_id(name, namespace_index), ua.QualifiedName(Name=name, NamespaceIndex=ua.Int16(namespace_index))
    )
    encoding = await create_encoding(
        node, encoding_id, ua.QualifiedName(Name=_ENCODING_NAME, NamespaceIndex=ua.Int16(0))
    )
    await encoding.add_reference(_DATA_TYPE_ENCODING_TYPE, _HAS_TYPE_DEFINITION)
    await node.write_data_type_definition(definition)


def _ua_field(each: _Field, namespace_index: int, known: dict[type, ua.NodeId]) -> ua.StructureField:
    if each.element in _SCALARS:
        datatype = ua.NodeId(ua.Int32(_SCALARS[each.element].value), ua.Int16(0))
    else:
        datatype = known.get(each.element) or _node_id(each.element.__name__, namespace_index)
    return ua.StructureField(
        Name=ua.String(each.name),
        DataType=datatype,
        ValueRank=ua.Int32(ua.ValueRank.OneDimension if each.array else ua.ValueRank.Scalar),
        ArrayDimensions=[ua.UInt32(0)] if each.array else [],  # 0: the length is not fixed
        # asyncua defaults to True; a plain structure has no mask to announce an optional field.
        IsOptional=False,
    )


def _check_structure_value(cls: type, value: object, *, array: bool) -> None:
    """Refuse a value that is not an instance of ``cls``, or with ``array`` not a list or tuple of them.

    asyncua stores any ExtensionObject in a variable regardless of the variable's DataType, so `UaVariable` runs
    this on every write of a structure variable, before its own ``write_check_callback``.

    :raises ua.UaStatusCodeError: ``BadTypeMismatch`` if the value has another type.
    """
    if array:
        if isinstance(value, (list, tuple)) and all(isinstance(each, cls) for each in value):
            return
    elif isinstance(value, cls):
        return
    raise ua.UaStatusCodeError(StatusCodes.BadTypeMismatch)
