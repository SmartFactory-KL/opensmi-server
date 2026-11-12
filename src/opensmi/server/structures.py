# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Structure DataTypes defined as dataclasses, for companion specifications without a nodeset.

A dataclass is the definition. `register_structure` creates the DataType, its Default Binary encoding and
its ``DataTypeDefinition`` on a server, and teaches asyncua the class, so instances can be stored in a
variable, written to it and read from it like any other value::

    @dataclass
    class Point:
        x: float = 0.0
        y: float = 0.0


    data_type = await register_structure(server, Point)
    position = UaVariable(Point(), name="Position", ua_data_type=data_type)

A `UaVariable` whose initial value is such a dataclass instance, or a list of them, does this itself, in
namespace 1 like enums.
A client needs no class of its own: ``Client.load_data_type_definitions()`` builds one from the
``DataTypeDefinition``.

**Field types.** Annotate fields with plain Python types, which stand for ``float``: Double, ``int``: Int64
(like a plain int in a `UaVariable`), ``str``: String, ``bool``: Boolean and ``bytes``: ByteString. Where a
field needs another OPC UA type, use the classes of ``asyncua.ua``: ``SByte``, ``Byte``, ``Int16``, ``UInt16``,
``Int32``, ``UInt32``, ``UInt64``, ``Float`` and the like. A field may also be another dataclass (registered
first, automatically) or a ``list`` of any of these for a one-dimensional array. Anything else is refused with a
`TypeError` that names the field. Because asyncua encodes by the annotations of the class and knows only its own
types, `register_structure` replaces the plain annotations of the class by the ``ua`` classes, in place.

**Names.** The NodeIds are strings derived from the class name (``<name>`` and ``<name>.DefaultBinary``), so
they do not depend on the order of registration. Registering again does nothing. A different class under a name
that is taken is refused with `StructureConflictError` before any node is created.

**One process.** asyncua keeps its table of structure classes per process, keyed by NodeId. Two servers in one
process that use the same namespace index and class name must therefore mean the same structure.
"""

import asyncio
import dataclasses
import types
import typing
import weakref
from enum import Enum
from typing import TYPE_CHECKING, Final, NamedTuple

from asyncua import ua
from asyncua.common.manage_nodes import create_encoding
from asyncua.ua.object_ids import ObjectIds
from asyncua.ua.status_codes import StatusCodes

if TYPE_CHECKING:
    from collections.abc import Callable

    from asyncua.server.server import Server

    from opensmi.server import Server as OpensmiServer

__all__ = ["StructureConflictError", "register_structure", "structure_array_write_check", "structure_class"]

_STRUCTURE: Final = ua.NodeId(ua.Int32(ObjectIds.Structure), ua.Int16(0))
_DATA_TYPE_ENCODING_TYPE: Final = ua.NodeId(ua.Int32(ObjectIds.DataTypeEncodingType), ua.Int16(0))
_HAS_TYPE_DEFINITION: Final = ua.NodeId(ua.Int32(ObjectIds.HasTypeDefinition), ua.Int16(0))
_ENCODING_SUFFIX: Final = ".DefaultBinary"

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

_Key = tuple[str, str, bool]


class StructureConflictError(ValueError):
    """A structure cannot be registered because its name is taken by a different definition."""


class _Field(NamedTuple):
    name: str
    element: type
    """A `_SCALARS` class, or a dataclass for a nested structure."""
    array: bool

    def key(self) -> _Key:
        """Return what makes two fields the same: the name, the type by name and whether it is an array."""
        return self.name, ("" if self.element in _SCALARS else "@") + self.element.__name__, self.array


class _Registry:
    """What was created on one server, by namespace index and name, as the keys of the fields."""

    def __init__(self) -> None:
        self.created: dict[tuple[int, str], tuple[_Key, ...]] = {}
        self.lock = asyncio.Lock()


_REGISTRIES: Final[weakref.WeakKeyDictionary[object, _Registry]] = weakref.WeakKeyDictionary()


def _fields(cls: type) -> list[_Field]:
    if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
        msg = f"{cls!r} is not a dataclass"
        raise TypeError(msg)
    hints = typing.get_type_hints(cls)
    fields = [_Field(each.name, *_element(cls, each.name, hints[each.name])) for each in dataclasses.fields(cls)]
    # asyncua encodes by the annotations of the class, and it only knows its own types: float would not encode
    for each in fields:
        cls.__annotations__[each.name] = list[each.element] if each.array else each.element
    return fields


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


def structure_class(value: object) -> type | None:
    """Return the dataclass of a value, or of the first element of a list, or ``None`` if it is not a structure."""
    if isinstance(value, list) and value:
        value = value[0]
    cls = type(value)
    return cls if _is_user_dataclass(cls) and not issubclass(cls, Enum) else None


def _collect(cls: type, found: dict[str, tuple[type, list[_Field]]]) -> None:
    """Add the structures ``cls`` needs to ``found``, nested ones first and ``cls`` itself last."""
    fields = _fields(cls)
    known = found.get(cls.__name__)
    if known is not None:
        _require_same(cls.__name__, tuple(each.key() for each in known[1]), tuple(each.key() for each in fields))
        return
    for each in fields:
        if each.element not in _SCALARS:
            _collect(each.element, found)
    found[cls.__name__] = (cls, fields)


def _require_same(name: str, known: tuple[_Key, ...], wanted: tuple[_Key, ...]) -> None:
    if known != wanted:
        listed = ", ".join(repr(each[0]) for each in known)
        msg = f"structure {name!r} is already defined with the fields {listed}, which differ"
        raise StructureConflictError(msg)


async def register_structure(
    server: "Server | OpensmiServer", cls: type, namespace_index: int | None = None
) -> ua.NodeId:
    """Create the DataType of a dataclass on a server, and of the dataclasses it nests, unless they exist.

    For each structure the DataType node comes first, then its Default Binary encoding node and last the
    ``DataTypeDefinition`` attribute. The classes are registered with asyncua as well, whether or not the nodes
    existed, so their instances encode and decode.

    :param server: The asyncua server whose address space gets the types, or the opensmi server that holds it.
    :param cls: The dataclass. Its name is the name of the DataType.
    :param namespace_index: The namespace of the types. By default the one registered last, so register your
        own namespace first.
    :raises TypeError: if ``cls`` is not a dataclass or a field has an unsupported type.
    :raises StructureConflictError: if a structure of that name exists with other fields, or ``cls`` nests two
        different classes of one name. Nothing is created in that case.
    :return: The NodeId of the DataType, to pass as ``ua_data_type`` of a variable.
    """
    ua_server = typing.cast("Server", getattr(server, "ua_server", server))
    found: dict[str, tuple[type, list[_Field]]] = {}
    _collect(cls, found)
    if namespace_index is None:
        namespace_index = len(await ua_server.get_namespace_array()) - 1
    registry = _REGISTRIES.setdefault(ua_server, _Registry())
    async with registry.lock:
        keys = {name: tuple(each.key() for each in fields) for name, (_, fields) in found.items()}
        for name, key in keys.items():
            known = registry.created.get((namespace_index, name))
            if known is not None:
                _require_same(name, known, key)
        for name, (each, fields) in found.items():
            if (namespace_index, name) not in registry.created:
                await _create(ua_server, namespace_index, name, fields)
                registry.created[namespace_index, name] = keys[name]
            ua.register_extension_object(
                name, _node_id(name + _ENCODING_SUFFIX, namespace_index), each, _node_id(name, namespace_index)
            )
    return _node_id(cls.__name__, namespace_index)


def _node_id(name: str, namespace_index: int) -> ua.NodeId:
    return ua.StringNodeId(Identifier=ua.String(name), NamespaceIndex=ua.Int16(namespace_index))


async def _create(server: "Server", namespace_index: int, name: str, fields: list[_Field]) -> None:
    encoding_id = _node_id(name + _ENCODING_SUFFIX, namespace_index)
    definition = ua.StructureDefinition(
        DefaultEncodingId=encoding_id,
        BaseDataType=_STRUCTURE,
        # Plain structure: every field is present, so there are no optional fields and no encoding mask.
        Fields=[_ua_field(each, namespace_index) for each in fields],
    )
    node = await server.nodes.base_structure_type.add_data_type(
        _node_id(name, namespace_index), ua.QualifiedName(Name=name, NamespaceIndex=ua.Int16(namespace_index))
    )
    encoding = await create_encoding(
        node, encoding_id, ua.QualifiedName(Name="Default Binary", NamespaceIndex=ua.Int16(0))
    )
    await encoding.add_reference(_DATA_TYPE_ENCODING_TYPE, _HAS_TYPE_DEFINITION)
    await node.write_data_type_definition(definition)


def _ua_field(each: _Field, namespace_index: int) -> ua.StructureField:
    if each.element in _SCALARS:
        datatype = ua.NodeId(ua.Int32(_SCALARS[each.element].value), ua.Int16(0))
    else:
        datatype = _node_id(each.element.__name__, namespace_index)
    return ua.StructureField(
        Name=ua.String(each.name),
        DataType=datatype,
        ValueRank=ua.Int32(ua.ValueRank.OneDimension if each.array else ua.ValueRank.Scalar),
        ArrayDimensions=[ua.UInt32(0)] if each.array else [],  # 0: the length is not fixed
        # asyncua defaults to True; a plain structure has no mask to announce an optional field.
        IsOptional=False,
    )


def structure_array_write_check(cls: type) -> "Callable[[object], None]":
    """Return a ``write_check_callback`` for a `UaVariable` that holds an array of ``cls``.

    asyncua stores any ExtensionObject in a variable regardless of the variable's DataType, so the check is
    needed to keep other values out. It refuses with ``BadTypeMismatch`` (`ua.UaStatusCodeError`) a value that
    is not a list or tuple of instances of ``cls``.
    """

    def check(value: object) -> None:
        if not isinstance(value, (list, tuple)) or not all(isinstance(each, cls) for each in value):
            raise ua.UaStatusCodeError(StatusCodes.BadTypeMismatch)

    return check
