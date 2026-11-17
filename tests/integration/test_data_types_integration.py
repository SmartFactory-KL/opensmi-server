# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from dataclasses import make_dataclass
from enum import IntEnum

import pytest
from asyncua import ua

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
