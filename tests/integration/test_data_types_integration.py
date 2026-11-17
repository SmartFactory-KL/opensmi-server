# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from dataclasses import make_dataclass
from enum import IntEnum

import pytest
from asyncua import ua
from asyncua.common.structures104 import new_enum

from opensmi.server.errors import TypeConflictError
from opensmi.server.structures import _create, _fields


def mode_enum() -> type[IntEnum]:
    """A new IntEnum class called Mode each time, as two modules (or a reload) would define it."""
    return IntEnum("Mode", ["OFF", "ON"], start=0)


async def enum_children(server) -> list[str]:
    children = await server.ua_server.nodes.enum_data_type.get_children()
    return [each.nodeid.to_string() for each in children if each.nodeid.NamespaceIndex == 1]


@pytest.mark.asyncio
async def test_ensure_enum_is_idempotent(empty_server) -> None:
    mode = mode_enum()

    first = await empty_server.ensure_enum(mode)

    assert await empty_server.ensure_enum(mode) == first
    assert len(await enum_children(empty_server)) == 1


@pytest.mark.asyncio
async def test_two_enums_of_one_name_are_refused(empty_server) -> None:
    await empty_server.ensure_enum(mode_enum())
    before = await enum_children(empty_server)

    with pytest.raises(TypeConflictError, match="Mode"):
        await empty_server.ensure_enum(mode_enum())

    assert await enum_children(empty_server) == before


@pytest.mark.asyncio
async def test_an_enum_and_a_structure_of_one_name_conflict_with_the_enum_first(empty_server) -> None:
    await empty_server.ensure_enum(mode_enum())

    with pytest.raises(TypeConflictError, match="Mode"):
        await empty_server.ensure_structure(make_dataclass("Mode", [("x", ua.Double, ua.Double(0.0))]))


@pytest.mark.asyncio
async def test_an_enum_and_a_structure_of_one_name_conflict_with_the_structure_first(empty_server) -> None:
    await empty_server.ensure_structure(make_dataclass("Mode", [("x", ua.Double, ua.Double(0.0))]))
    before = await enum_children(empty_server)

    with pytest.raises(TypeConflictError, match="Mode"):
        await empty_server.ensure_enum(mode_enum())

    assert await enum_children(empty_server) == before


@pytest.mark.asyncio
async def test_a_created_structure_cannot_take_the_name_of_a_bound_one(empty_server) -> None:
    # as from an imported nodeset: the nodes of a DataType Remote, and no class for it
    remote = make_dataclass("Remote", [("x", ua.Double, ua.Double(0.0))])
    await _create(empty_server.ua_server, 1, "Remote", _fields(remote), {})
    bound = make_dataclass("Bound", [("x", ua.Double, ua.Double(0.0))])
    await empty_server.register_structure(bound, ua_data_type=ua.NodeId("Remote", 1))

    with pytest.raises(TypeConflictError, match="Remote"):
        await empty_server.ensure_structure(make_dataclass("Remote", [("x", ua.Double, ua.Double(0.0))]))


async def nodeset_enum(server, name: str, members: list[str]) -> ua.NodeId:
    """Create an enumeration DataType in namespace 1 as an imported nodeset would: the nodes, and no class for it."""
    node = await new_enum(server=server.ua_server, idx=1, name=name, fields=members)
    return node.nodeid


@pytest.mark.asyncio
async def test_register_enum_binds_a_matching_enum(empty_server) -> None:
    remote = await nodeset_enum(empty_server, "Remote", ["OFF", "ON"])
    mode = mode_enum()

    await empty_server.register_enum(mode, ua_data_type=remote)
    await empty_server.register_enum(mode, ua_data_type=remote)  # the same again does nothing

    assert await empty_server.ensure_enum(mode) == remote


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "members",
    [["OFF", "ON", "AUTO"], ["OFF"], ["ON", "OFF"], ["OFF", "RUNNING"]],
    ids=["extra member", "missing member", "renumbered", "renamed"],
)
async def test_register_enum_refuses_an_enum_whose_members_differ(empty_server, members) -> None:
    remote = await nodeset_enum(empty_server, "Remote", members)
    mode = mode_enum()

    with pytest.raises(TypeConflictError, match="members"):
        await empty_server.register_enum(mode, ua_data_type=remote)

    assert mode not in empty_server._types.by_class


@pytest.mark.asyncio
@pytest.mark.parametrize("prop", ["EnumValues", "EnumStrings"])
async def test_register_enum_compares_enum_values_or_enum_strings(empty_server, prop) -> None:
    remote = await empty_server.ua_server.nodes.enum_data_type.add_data_type(
        ua.NodeId("Remote", 1), ua.QualifiedName("Remote", 1)
    )
    if prop == "EnumValues":
        value = [
            ua.EnumValueType(Value=index, DisplayName=ua.LocalizedText(name))
            for index, name in enumerate(["OFF", "AUTO"])
        ]
    else:
        value = [ua.LocalizedText(name) for name in ["OFF", "AUTO"]]
    await remote.add_property(0, prop, value)

    with pytest.raises(TypeConflictError, match="AUTO"):
        await empty_server.register_enum(mode_enum(), ua_data_type=remote.nodeid)


@pytest.mark.asyncio
async def test_register_enum_accepts_a_data_type_without_members_with_a_warning(empty_server, caplog) -> None:
    bare = await empty_server.ua_server.nodes.enum_data_type.add_data_type(
        ua.NodeId("Bare", 1), ua.QualifiedName("Bare", 1)
    )
    mode = mode_enum()

    await empty_server.register_enum(mode, ua_data_type=bare.nodeid)

    assert empty_server._types.by_class[mode] == bare.nodeid
    assert "publishes no members" in caplog.text


@pytest.mark.asyncio
async def test_register_enum_refuses_another_data_type_unless_overwrite(empty_server) -> None:
    first = await nodeset_enum(empty_server, "First", ["OFF", "ON"])
    second = await nodeset_enum(empty_server, "Second", ["OFF", "ON"])
    mode = mode_enum()
    await empty_server.register_enum(mode, ua_data_type=first)

    with pytest.raises(TypeConflictError, match="overwrite"):
        await empty_server.register_enum(mode, ua_data_type=second)
    await empty_server.register_enum(mode, ua_data_type=second, overwrite=True)

    assert empty_server._types.by_class[mode] == second
    assert (1, "First") not in empty_server._types.by_name


@pytest.mark.asyncio
async def test_register_enum_refuses_a_data_type_that_another_enum_has(empty_server) -> None:
    owned = await empty_server.ensure_enum(mode_enum())

    with pytest.raises(TypeConflictError, match="already belongs"):
        await empty_server.register_enum(mode_enum(), ua_data_type=owned)
