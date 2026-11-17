# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from dataclasses import make_dataclass
from enum import IntEnum

import pytest
from asyncua import ua
from asyncua.common.structures104 import new_enum

from opensmi.server import BaseMachine, ParameterSet, UaVariable
from opensmi.server.errors import TypeConflictError
from opensmi.server.mixins import ParameterSetMixin
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


class Machine(BaseMachine):
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("1")
        await self.identification.ProductInstanceUri.write("urn:example:1")
        await self.identification.Manufacturer.write(ua.LocalizedText("Example", "en"))


class Level(IntEnum):
    LOW = 0
    HIGH = 1


def machine_with(variable: UaVariable) -> Machine:
    """A machine whose ParameterSet holds ``variable`` as ``Value``."""

    class Parameters(ParameterSet):
        Value = variable

    class WithVariable(ParameterSetMixin[Parameters], Machine):
        pass

    return WithVariable()


@pytest.mark.asyncio
async def test_an_enum_variable_uses_an_explicit_ua_data_type(empty_server) -> None:
    remote = await nodeset_enum(empty_server, "Remote", ["LOW", "HIGH"])

    machine = await empty_server.add_machine(machine_with(UaVariable(Level.HIGH, ua_data_type=remote)))

    assert (await machine.parameter_set.Value.ua_node.read_data_type()) == remote
    assert Level not in empty_server._types.by_class  # used as given, not bound
    assert await machine.parameter_set.Value.read() is Level.HIGH


@pytest.mark.asyncio
async def test_an_enum_variable_uses_the_bound_data_type(empty_server) -> None:
    remote = await nodeset_enum(empty_server, "Remote", ["LOW", "HIGH"])
    await empty_server.register_enum(Level, ua_data_type=remote)

    machine = await empty_server.add_machine(machine_with(UaVariable(Level.LOW)))

    assert (await machine.parameter_set.Value.ua_node.read_data_type()) == remote
    assert await enum_children(empty_server) == [remote.to_string()]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "variable",
    [
        lambda: UaVariable(Level.LOW, unit="cm", range=(0, 1)),
        lambda: UaVariable(Level.LOW, range=(0, 1)),
        lambda: UaVariable(make_dataclass("Sized", [("x", ua.Double, ua.Double(0.0))])(), unit="cm", range=(0, 1)),
    ],
    ids=["enum with unit", "enum with range", "structure with unit"],
)
async def test_enums_and_structures_refuse_units_and_ranges(empty_server, variable) -> None:
    with pytest.raises(ValueError, match="units or ranges"):
        await empty_server.add_machine(machine_with(variable()))


class Gapped(IntEnum):
    ONE = 1
    TWO = 2
    FOUR = 4


@pytest.mark.asyncio
async def test_ensure_enum_refuses_values_that_are_not_0_to_n(empty_server) -> None:
    with pytest.raises(ValueError, match="must start at 0 and increase by 1 per member"):
        await empty_server.ensure_enum(Gapped)

    assert await enum_children(empty_server) == []


@pytest.mark.asyncio
async def test_a_variable_of_an_enum_with_gaps_is_refused_when_its_node_is_created(empty_server) -> None:
    with pytest.raises(ValueError, match="register_enum"):
        await empty_server.add_machine(machine_with(UaVariable(Gapped.ONE)))


@pytest.mark.asyncio
async def test_an_enum_with_gaps_bound_to_a_nodeset_type_can_be_held_by_a_variable(empty_server) -> None:
    remote = await empty_server.ua_server.nodes.enum_data_type.add_data_type(
        ua.NodeId("Remote", 1), ua.QualifiedName("Remote", 1)
    )
    await remote.add_property(
        0,
        "EnumValues",
        [ua.EnumValueType(Value=member.value, DisplayName=ua.LocalizedText(member.name)) for member in Gapped],
    )
    await empty_server.register_enum(Gapped, ua_data_type=remote.nodeid)

    machine = await empty_server.add_machine(machine_with(UaVariable(Gapped.FOUR, writable=True, bypass_lock=True)))
    value = machine.parameter_set.Value

    assert (await value.ua_node.read_data_type()) == remote.nodeid
    assert await value.read() is Gapped.FOUR
    await value.write(Gapped.TWO)
    assert await value.read() is Gapped.TWO


@pytest.mark.asyncio
async def test_a_created_enum_has_its_name_as_node_id(empty_server) -> None:
    assert await empty_server.ensure_enum(Level) == ua.NodeId("Level", 1)
    assert await empty_server.ensure_enum(mode_enum()) == ua.NodeId("Mode", 1)


@pytest.mark.asyncio
async def test_a_machine_named_like_a_created_enum_clashes(empty_server) -> None:
    # a machine's NodeId is ns=1;s=<name> like a created type's, until instance NodeIds change; the error is not clear
    machine = machine_with(UaVariable(0))
    await empty_server.ensure_enum(IntEnum(machine.name, ["A"], start=0))

    with pytest.raises(ua.UaStatusCodeError):
        await empty_server.add_machine(machine)


@pytest.mark.asyncio
async def test_an_enum_named_like_a_machine_clashes(empty_server) -> None:
    machine = await empty_server.add_machine(machine_with(UaVariable(0)))

    with pytest.raises(ua.uaerrors.BadNodeIdExists):
        await empty_server.ensure_enum(IntEnum(machine.name, ["A"], start=0))
