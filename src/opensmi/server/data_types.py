# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""The OPC UA DataTypes a `Server` mints or binds for Python classes: enumerations and structures.

Every class has at most one DataType per server, and a type name is used by at most one class per namespace, for
enumerations and structures alike: a client that generates code from the address space would get two classes of one
name otherwise. A class that breaks this is refused with `opensmi.server.errors.TypeConflictError` before anything is
created.

Enumerations are created by `Server.ensure_enum` (in namespace 1) or bound to an existing DataType with
`Server.register_enum`; structures are described in `opensmi.server.structures`.

Everything is reached through `Server` and `UaVariable`; the contents of this module are private.
"""

import asyncio
from enum import IntEnum
from typing import TYPE_CHECKING

from asyncua import ua
from asyncua.common.structures104 import new_enum

from opensmi.server.errors import TypeConflictError

__all__: list[str] = []  # everything here is private; use Server and UaVariable

if TYPE_CHECKING:
    from asyncua.common.node import Node
    from asyncua.server.server import Server


class _TypeRegistry:
    """What a `Server` knows about the DataTypes of its classes. Lives as long as the server and is never reset."""

    def __init__(self) -> None:
        """Create an empty registry."""
        self.lock = asyncio.Lock()
        """Held while types are created or bound."""
        self.by_class: dict[type, ua.NodeId] = {}
        """The one DataType of each class that has one, whether created here or bound to an existing one."""
        self.by_name: dict[tuple[int, str], type] = {}
        """The class that uses each type name, by namespace index and BrowseName, created or bound."""

    def check_name(self, namespace_index: int, name: str, cls: type) -> None:
        """Refuse ``cls`` a type name that a different class uses.

        :raises TypeConflictError: if another class has ``name`` in that namespace.
        """
        owner = self.by_name.get((namespace_index, name))
        if owner is not None and owner is not cls:
            raise _name_taken(name, owner, cls, namespace_index)


def _qualified(cls: type) -> str:
    return f"{cls.__module__}.{cls.__qualname__}"


def _name_taken(name: str, owner: type, cls: type, namespace_index: int | None = None) -> TypeConflictError:
    where = "" if namespace_index is None else f" in namespace {namespace_index}"
    msg = (
        f"the type name {name!r}{where} is already taken by {_qualified(owner)}, so {_qualified(cls)} cannot have "
        "it, even with the same fields or members"
    )
    return TypeConflictError(msg)


async def _ensure_enum(registry: _TypeRegistry, server: "Server", enum_type: type[IntEnum]) -> ua.NodeId:
    """Create the DataType of an enumeration in namespace 1, unless it has one. This is `Server.ensure_enum`."""
    async with registry.lock:
        if (known := registry.by_class.get(enum_type)) is not None:
            return known
        name = enum_type.__name__
        _require_consecutive(enum_type)
        registry.check_name(1, name, enum_type)
        node = await new_enum(server=server, idx=1, name=name, fields=[enum_type(index).name for index in enum_type])
        registry.by_class[enum_type] = node.nodeid
        registry.by_name[1, name] = enum_type
        return node.nodeid


def _require_consecutive(enum_type: type[IntEnum]) -> None:
    """Refuse an enum that `new_enum` cannot describe: it publishes ``EnumStrings``, where the position is the value."""
    values = [member.value for member in enum_type]
    expected = list(range(len(values)))
    if values != expected:
        msg = (
            f"OPC UA compatible IntEnums must start at 0 and increase by 1 per member; "
            f"{enum_type.__name__} has values {values}, expected {expected}. Bind an enum with other values to an "
            "existing DataType with register_enum instead."
        )
        raise ValueError(msg)


async def _register_enum(
    registry: _TypeRegistry, server: "Server", enum_type: type[IntEnum], ua_data_type: ua.NodeId, *, overwrite: bool
) -> bool:
    """Bind an enumeration to an existing DataType. This is `Server.register_enum`.

    :return: Whether the DataType published its members, so the enumeration could be compared with them.
    """
    node = server.get_node(ua_data_type)
    published = await _published_members(node)
    browse_name = await node.read_browse_name()
    async with registry.lock:
        known = registry.by_class.get(enum_type)
        if known is not None and known != ua_data_type and not overwrite:
            msg = (
                f"{enum_type.__name__} already has the DataType {known.to_string()}, not {ua_data_type.to_string()}; "
                "pass overwrite=True to bind it to the other one"
            )
            raise TypeConflictError(msg)
        for other, data_type in registry.by_class.items():
            if data_type == ua_data_type and other is not enum_type:
                msg = (
                    f"the DataType {ua_data_type.to_string()} already belongs to {_qualified(other)}, so "
                    f"{_qualified(enum_type)} cannot be bound to it"
                )
                raise TypeConflictError(msg)
        registry.check_name(browse_name.NamespaceIndex, browse_name.Name, enum_type)
        if published is not None:
            _require_same_members(enum_type, ua_data_type, published)
        if known is not None and known != ua_data_type:  # overwrite: the enum gives up its old type name
            for key in [key for key, owner in registry.by_name.items() if owner is enum_type]:
                del registry.by_name[key]
        registry.by_class[enum_type] = ua_data_type
        registry.by_name[browse_name.NamespaceIndex, browse_name.Name] = enum_type
    return published is not None


async def _published_members(node: "Node") -> dict[str, int] | None:
    """Return the members an enumeration DataType publishes, by name, or ``None`` if it publishes none.

    Looks at the ``DataTypeDefinition`` first, then the ``EnumValues`` and the ``EnumStrings`` property.
    """
    try:
        definition = await node.read_data_type_definition()
    except ua.UaStatusCodeError:
        definition = None
    if isinstance(definition, ua.EnumDefinition):
        return {each.Name: each.Value for each in definition.Fields}
    for name in ("EnumValues", "EnumStrings"):
        try:
            values = await (await node.get_child(f"0:{name}")).read_value()
        except ua.UaStatusCodeError:
            continue
        if name == "EnumValues":
            return {each.DisplayName.Text: each.Value for each in values}
        return {each.Text: index for index, each in enumerate(values)}
    return None


def _require_same_members(enum_type: type[IntEnum], ua_data_type: ua.NodeId, published: dict[str, int]) -> None:
    wanted = {member.name: member.value for member in enum_type}
    if wanted == published:
        return
    differing = sorted(name for name in wanted.keys() | published.keys() if wanted.get(name) != published.get(name))
    msg = (
        f"{enum_type.__name__} does not match the DataType {ua_data_type.to_string()}: the members {differing} differ "
        f"(the enum has {wanted}, the DataType {published})"
    )
    raise TypeConflictError(msg)
