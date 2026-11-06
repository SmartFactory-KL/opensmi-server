# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Structure DataTypes minted from a plain description, with a binary codec and a write check.

A companion specification defines its structures in a nodeset. When there is none to import, this
module makes one DataType from a description (`StructureDefinition`), without generated Python
classes and without scanning the address space:

- `StructureRegistry.mint` creates the DataType under ``BaseStructure``, its *Default Binary*
  encoding node (with a ``HasTypeDefinition`` reference to ``DataTypeEncodingType``) and, last,
  the ``DataTypeDefinition`` attribute, because the definition names the encoding.
- `encode` and `decode` turn a mapping from field names to values into the `ua.ExtensionObject`
  a client reads and back, built from asyncua's primitive packers in the order of the fields.
- `structure_array_write_check` is a ``write_check_callback`` for `UaVariable` that refuses a
  value that is not an array of the structure.

Both NodeIds are string identifiers derived from the type name (``<type_name>`` and
``<type_name>.DefaultBinary``), so they do not depend on the order in which types are minted.
A client discovers the shape the standard way, through ``DataTypeDefinition``, for example with
``Client.load_data_type_definitions()``.

**Idempotent per server.** A `StructureRegistry` remembers what it minted; minting the same
definition again does nothing. Minting a *different* definition under a name that is taken is
refused with `StructureConflictError` before any node is created. Use `registry_for` to share one
registry per server.

**The codec refuses rather than approximates.** A value that is not an `ua.ExtensionObject`, one
with a foreign encoding id, a body that ends inside a field, a body with bytes left over and a
null array, string or ByteString are each refused with a `ValueError`. A fixed-size array of the
wrong length is refused likewise.

**Nesting.** A field whose ``datatype`` is another `StructureDefinition` nests that structure. It
is minted before the structure that names it, and its elements are encoded inline, as their
bodies without an ExtensionObject header (OPC UA Part 6).
"""

from __future__ import annotations

import asyncio
import weakref
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Final

from asyncua import ua
from asyncua.common.manage_nodes import create_encoding
from asyncua.common.utils import Buffer
from asyncua.ua.object_ids import ObjectIds
from asyncua.ua.status_codes import StatusCodes
from asyncua.ua.ua_binary import Primitives

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from asyncua.server.server import Server

__all__ = [
    "ENCODING_SUFFIX",
    "StructureConflictError",
    "StructureDefinition",
    "StructureField",
    "StructureRegistry",
    "decode",
    "encode",
    "encoding_node_id",
    "registry_for",
    "structure_array_write_check",
    "structure_node_id",
]

ENCODING_SUFFIX: Final = ".DefaultBinary"
"""What tells the identifier of an encoding node from the identifier of its DataType."""

_STRUCTURE: Final = ua.NodeId(ua.Int32(ObjectIds.Structure), ua.Int16(0))
_DATA_TYPE_ENCODING_TYPE: Final = ua.NodeId(ua.Int32(ObjectIds.DataTypeEncodingType), ua.Int16(0))
_HAS_TYPE_DEFINITION: Final = ua.NodeId(ua.Int32(ObjectIds.HasTypeDefinition), ua.Int16(0))
_DEFAULT_BINARY: Final = "Default Binary"
_UNKNOWN_LENGTH: Final = 0
"""The ArrayDimensions entry of an array field of unknown length."""

_BODY_IS_BINARY: Final = 1
"""The ExtensionObject encoding byte that says the body is a ByteString, which ours always is."""

_NULL_LENGTH: Final = -1
"""The length OPC UA writes for a null array, string or ByteString."""

_PACKERS: Final[dict[ua.VariantType, Any]] = {
    variant: getattr(Primitives, variant.name)
    for variant in (
        ua.VariantType.Boolean,
        ua.VariantType.SByte,
        ua.VariantType.Byte,
        ua.VariantType.Int16,
        ua.VariantType.UInt16,
        ua.VariantType.Int32,
        ua.VariantType.UInt32,
        ua.VariantType.Int64,
        ua.VariantType.UInt64,
        ua.VariantType.Float,
        ua.VariantType.Double,
        ua.VariantType.String,
        ua.VariantType.ByteString,
    )
}
"""asyncua's own packer for each built-in type a field can carry, found by the type's name."""

_NULLABLE: Final = frozenset({ua.VariantType.String, ua.VariantType.ByteString})


class StructureConflictError(ValueError):
    """A structure cannot be minted because its name is taken by a different definition."""


@dataclass(frozen=True, slots=True, kw_only=True)
class StructureField:
    """One field of a `StructureDefinition`.

    Only these built-in types can be fields: ``Boolean``, ``SByte``, ``Byte``, ``Int16``,
    ``UInt16``, ``Int32``, ``UInt32``, ``Int64``, ``UInt64``, ``Float``, ``Double``, ``String``
    and ``ByteString``. A nested structure is named through `datatype`.
    """

    name: str
    """The field name, as a client sees it in the definition."""
    variant_type: ua.VariantType
    """The built-in type of the field, or of each element of an array. ``ExtensionObject`` for a nested structure."""
    datatype: StructureDefinition | ua.NodeId | None = None
    """What the DataType is where the built-in type does not say it: a nested structure (minted first) or the
    `ua.NodeId` of a type that is already on the server. ``None`` means the built-in type."""
    array: bool = False
    """Whether the field is a one-dimensional array."""
    length: int = 0
    """The length of a fixed-size array, which the definition publishes. Zero means unknown length."""

    def __post_init__(self) -> None:
        """Refuse a length on a field that is not an array.

        :raises ValueError: if a length is given without ``array``, or a negative one.
        """
        if self.length < 0 or (self.length and not self.array):
            msg = f"field {self.name!r} has length {self.length} but is not an array of that size"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True, kw_only=True)
class StructureDefinition:
    """The description of one structure DataType. Equal definitions mean the same type."""

    type_name: str
    """The name of the type. It is the identifier of the DataType's NodeId and its BrowseName."""
    fields: tuple[StructureField, ...] = field(default_factory=tuple)
    """The fields in the order in which they are encoded."""

    def dependencies(self) -> tuple[StructureDefinition, ...]:
        """Return every structure this one names, nested ones first and itself last.

        The order is depth first in the order of the fields. A definition met twice is listed once;
        two different definitions of one name are both listed so that the registry can refuse them.
        """
        ordered: list[StructureDefinition] = []
        _collect(self, ordered)
        return tuple(ordered)


def _collect(definition: StructureDefinition, ordered: list[StructureDefinition]) -> None:
    if definition in ordered:
        return
    for each in definition.fields:
        if isinstance(each.datatype, StructureDefinition):
            _collect(each.datatype, ordered)
    ordered.append(definition)


def structure_node_id(type_name: str, namespace_index: int) -> ua.NodeId:
    """Return the NodeId of the DataType called ``type_name``: a string identifier equal to the name."""
    return ua.StringNodeId(Identifier=ua.String(type_name), NamespaceIndex=ua.Int16(namespace_index))


def encoding_node_id(type_name: str, namespace_index: int) -> ua.NodeId:
    """Return the NodeId of the Default Binary encoding of the structure called ``type_name``.

    This, and not `structure_node_id`, is what an ExtensionObject carries as its ``TypeId``.
    """
    return ua.StringNodeId(
        Identifier=ua.String(f"{type_name}{ENCODING_SUFFIX}"),
        NamespaceIndex=ua.Int16(namespace_index),
    )


class StructureRegistry:
    """The structures minted on one server, and the one place where they are minted.

    Keep one per server (see `registry_for`). It remembers which definition each type was minted from.
    """

    def __init__(self) -> None:
        """Start out having minted nothing."""
        self._minted: dict[tuple[int, str], StructureDefinition] = {}
        self._lock = asyncio.Lock()

    @property
    def minted(self) -> tuple[str, ...]:
        """The names of the types minted so far, in the order in which they were minted."""
        return tuple(name for _, name in self._minted)

    def get(self, type_name: str, namespace_index: int) -> StructureDefinition | None:
        """Return the definition a type was minted from, or ``None`` if it was not minted."""
        return self._minted.get((namespace_index, type_name))

    async def mint(self, server: Server, namespace_index: int, definition: StructureDefinition) -> ua.NodeId:
        """Create the structure and the structures it nests, unless they exist, and return its NodeId.

        For each structure the DataType node comes first, then its Default Binary encoding node and last
        the ``DataTypeDefinition`` attribute.

        :param server: The asyncua server whose address space gets the types.
        :param namespace_index: The namespace index in which the types are created.
        :param definition: The structure to mint.
        :raises StructureConflictError: if a structure of that name was minted with other fields, or if
            ``definition`` names two different structures under one name. Nothing is created in that case.
        :return: The NodeId of the DataType.
        """
        async with self._lock:
            for each in self._plan(namespace_index, definition):
                await self._create(server, namespace_index, each)
        return structure_node_id(definition.type_name, namespace_index)

    def _plan(self, namespace_index: int, definition: StructureDefinition) -> list[StructureDefinition]:
        """Return what still has to be created, after every check has passed."""
        planned: dict[str, StructureDefinition] = {}
        for each in definition.dependencies():
            known = self._minted.get((namespace_index, each.type_name)) or planned.get(each.type_name)
            if known is None:
                planned[each.type_name] = each
            elif known != each:
                listed = ", ".join(repr(f.name) for f in known.fields)
                msg = f"structure {each.type_name!r} is already defined with the fields {listed}, which differ"
                raise StructureConflictError(msg)
        return [each for each in planned.values() if (namespace_index, each.type_name) not in self._minted]

    async def _create(self, server: Server, namespace_index: int, definition: StructureDefinition) -> None:
        node_id = structure_node_id(definition.type_name, namespace_index)
        encoding_id = encoding_node_id(definition.type_name, namespace_index)
        ua_definition = ua.StructureDefinition(
            DefaultEncodingId=encoding_id,
            BaseDataType=_STRUCTURE,
            # Plain structure: every field is present, so there are no optional fields and no encoding mask.
            Fields=[_ua_field(each, namespace_index) for each in definition.fields],
        )
        node = await server.nodes.base_structure_type.add_data_type(
            node_id,
            ua.QualifiedName(Name=definition.type_name, NamespaceIndex=ua.Int16(namespace_index)),
        )
        encoding = await create_encoding(
            node, encoding_id, ua.QualifiedName(Name=_DEFAULT_BINARY, NamespaceIndex=ua.Int16(0))
        )
        await encoding.add_reference(_DATA_TYPE_ENCODING_TYPE, _HAS_TYPE_DEFINITION)
        await node.write_data_type_definition(ua_definition)
        self._minted[(namespace_index, definition.type_name)] = definition


def _ua_field(each: StructureField, namespace_index: int) -> ua.StructureField:
    if isinstance(each.datatype, StructureDefinition):
        datatype = structure_node_id(each.datatype.type_name, namespace_index)
    else:
        datatype = each.datatype or ua.NodeId(ua.Int32(each.variant_type.value), ua.Int16(0))
    return ua.StructureField(
        Name=ua.String(each.name),
        DataType=datatype,
        ValueRank=ua.Int32(ua.ValueRank.OneDimension if each.array else ua.ValueRank.Scalar),
        ArrayDimensions=[ua.UInt32(each.length or _UNKNOWN_LENGTH)] if each.array else [],
        # asyncua defaults to True; a plain structure has no mask to announce an optional field.
        IsOptional=False,
    )


_REGISTRIES: Final[weakref.WeakKeyDictionary[Any, StructureRegistry]] = weakref.WeakKeyDictionary()


def registry_for(server: Server) -> StructureRegistry:
    """Return the registry of an asyncua server, creating it on first use.

    The NodeIds are derived from the type names, so two registries on one server would collide.
    """
    registry = _REGISTRIES.get(server)
    if registry is None:
        registry = _REGISTRIES[server] = StructureRegistry()
    return registry


def encode(definition: StructureDefinition, mapping: Mapping[str, object], namespace_index: int) -> ua.ExtensionObject:
    """Encode one value of a structure as the `ua.ExtensionObject` a client reads.

    :param definition: The structure the value is of.
    :param mapping: The fields by name. An array is any sequence, a nested structure is a mapping and a
        ByteString is ``bytes``.
    :param namespace_index: The namespace index in which the structure was minted.
    :raises KeyError: if a field has no value.
    :raises ValueError: if a fixed-size array has the wrong length or a field has a type that cannot be packed.
    """
    body = _pack_body(definition, mapping)
    carried = ua.ExtensionObject(
        TypeId=encoding_node_id(definition.type_name, namespace_index), Body=ua.ByteString(body)
    )
    # ua.ExtensionObject is frozen and asyncua fills in Encoding only for classes it knows. Without the
    # byte the object goes on the wire announcing no body and then carrying one.
    object.__setattr__(carried, "Encoding", _BODY_IS_BINARY)
    return carried


def decode(definition: StructureDefinition, value: object, namespace_index: int) -> dict[str, object]:
    """Decode a `ua.ExtensionObject` of a structure into its fields by name: the inverse of `encode`.

    :param definition: The structure the value is of.
    :param value: What arrived as one element.
    :param namespace_index: The namespace index in which the structure was minted.
    :raises ValueError: if the value is not an ExtensionObject of this structure, if the body is shorter or
        longer than its fields account for, or if a field is null.
    """
    wanted = encoding_node_id(definition.type_name, namespace_index)
    if not isinstance(value, ua.ExtensionObject):
        msg = f"{type(value).__name__} is not an ExtensionObject of {definition.type_name}"
        raise ValueError(msg)  # noqa: TRY004
    if value.TypeId != wanted:
        msg = (
            f"the type id {value.TypeId.to_string()} is not the encoding {wanted.to_string()} of {definition.type_name}"
        )
        raise ValueError(msg)
    body = Buffer(bytes(value.Body or b""))
    decoded = _unpack_body(definition, body)
    if len(body):
        msg = f"the body of {definition.type_name} has {len(body)} bytes more than its {len(definition.fields)} fields"
        raise ValueError(msg)
    return decoded


def structure_array_write_check(definition: StructureDefinition, namespace_index: int) -> Callable[[object], None]:
    """Return a ``write_check_callback`` for a `UaVariable` that holds an array of the structure.

    asyncua stores any ExtensionObject in a variable regardless of the variable's DataType, so the
    check is needed to keep other values out.

    The callback refuses with ``BadTypeMismatch`` (`ua.UaStatusCodeError`) a value that is not a list or tuple of
    ExtensionObjects that `decode` reads whole.
    """

    def check(value: object) -> None:
        if not isinstance(value, (list, tuple)):
            raise ua.UaStatusCodeError(StatusCodes.BadTypeMismatch)
        try:
            for each in value:
                decode(definition, each, namespace_index)
        except ValueError as err:
            raise ua.UaStatusCodeError(StatusCodes.BadTypeMismatch) from err

    return check


def _packer(each: StructureField) -> Any:
    packer = _PACKERS.get(each.variant_type)
    if packer is None:
        msg = f"field {each.name!r} is of type {each.variant_type.name}, which cannot be a field of a structure here"
        raise ValueError(msg)
    return packer


def _require_length(each: StructureField, count: int) -> None:
    if each.length and count != each.length:
        msg = f"field {each.name!r} is an array of {each.length} elements, and {count} were given"
        raise ValueError(msg)


def _pack_body(definition: StructureDefinition, values: Mapping[str, object]) -> bytes:
    return b"".join(_pack(each, values[each.name]) for each in definition.fields)


def _pack(each: StructureField, value: object) -> bytes:
    if not each.array:
        return _pack_one(each, value)
    elements = list(value)  # pyright: ignore[reportArgumentType, reportCallIssue]
    _require_length(each, len(elements))
    return Primitives.Int32.pack(len(elements)) + b"".join(_pack_one(each, element) for element in elements)


def _pack_one(each: StructureField, value: object) -> bytes:
    if isinstance(each.datatype, StructureDefinition):
        return _pack_body(each.datatype, value)  # pyright: ignore[reportArgumentType]
    return _packer(each).pack(value)


def _unpack_body(definition: StructureDefinition, body: Buffer) -> dict[str, object]:
    return {each.name: _unpack(each, body) for each in definition.fields}


def _unpack(each: StructureField, body: Buffer) -> object:
    try:
        if not each.array:
            return _unpack_one(each, body)
        count = Primitives.Int32.unpack(body)
        if count == _NULL_LENGTH:
            msg = f"field {each.name!r} is a null array, which is not an empty one"
            raise ValueError(msg)
        if count < 0:
            msg = f"field {each.name!r} has an array of {count} elements"
            raise ValueError(msg)
        _require_length(each, count)
        return [_unpack_one(each, body) for _ in range(count)]
    except ua.UaError as exhausted:
        msg = f"field {each.name!r} runs off the end of the body: {exhausted}"
        raise ValueError(msg) from exhausted


def _unpack_one(each: StructureField, body: Buffer) -> object:
    if isinstance(each.datatype, StructureDefinition):
        return _unpack_body(each.datatype, body)
    if each.variant_type in _NULLABLE and Primitives.Int32.unpack(body.copy()) < 0:
        msg = f"field {each.name!r} is null, which is not an empty value"
        raise ValueError(msg)
    return _packer(each).unpack(body)
