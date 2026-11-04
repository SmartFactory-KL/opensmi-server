# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Paths, naming rules and shared listings on the server."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from asyncua import ua
from asyncua.ua.object_ids import ObjectIds
from opensmi.core import ObjectPath
from opensmi.core.nodesets import MachineryNodeIds
from structlog.testing import capture_logs
from typing_extensions import override

from opensmi.server import (
    BaseComponent,
    BaseMachine,
    BaseMachineryItem,
    BaseMethod,
    BaseSkillFinite,
    Monitoring,
    ParameterSet,
    Server,
    UaVariable,
)
from opensmi.server.base_feasibility_check import BaseFeasibilityCheck
from opensmi.server.base_precondition_check import BasePreconditionCheck
from opensmi.server.mixins import MonitoringMixin, NotificationForwarderMixin, ParameterSetMixin, ParentMixin


class Machine(BaseMachine):
    @override
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(ua.LocalizedText("SmartFactory KL", "en"))


class _Skill(BaseSkillFinite):
    @override
    async def _handle_running(self) -> None:
        pass


class _FeasibilityCheck(BaseFeasibilityCheck):
    @override
    async def _handle_running(self) -> None:
        pass


class _PreconditionCheck(BasePreconditionCheck):
    @override
    async def _handle_running(self) -> None:
        pass


class _Method(ParentMixin[BaseMachineryItem], BaseMethod):
    @override
    async def execute_method(self) -> None:
        pass


class _Component(ParentMixin[BaseMachineryItem], NotificationForwarderMixin, BaseComponent):
    @override
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("component-1")
        await self.identification.Manufacturer.write(ua.LocalizedText("SmartFactory KL", "en"))

    @override
    async def _init(self) -> None:
        await super()._init()


class _Parameters(ParameterSet):
    Force = UaVariable(1.0)


class _Monitoring(Monitoring):
    Temperature = UaVariable(20.0)


class _GroupedMachine(MonitoringMixin[_Monitoring], ParameterSetMixin[_Parameters], Machine):
    pass


@pytest_asyncio.fixture
async def grouped(server) -> AsyncGenerator[_GroupedMachine]:
    machine = _GroupedMachine(name="M1")
    async with server:
        await server.add_machine(machine)
        yield machine


# --------------------------------------------------------------------------- folders and check paths


@pytest.mark.asyncio
async def test_objects_lie_in_their_opensmi_folders(server: Server):
    machine = Machine(name="M1")
    async with server:
        await server.add_machine(machine)
        skill = await machine.add_skill(_Skill(name="Pick"))
        await skill.add_feasibility_check(_FeasibilityCheck())
        await skill.add_precondition_check(_PreconditionCheck())
        await machine.add_method(_Method(name="Pick"))
        await machine.add_component(_Component(name="Gripper"))

        assert server.resolve("/M1/SkillSet/Pick") is skill
        assert type(server.resolve("/M1/SkillSet/Pick/FeasibilityCheck")) is _FeasibilityCheck
        assert type(server.resolve("/M1/SkillSet/Pick/PreconditionCheck")) is _PreconditionCheck
        assert type(server.resolve("/M1/MethodSet/Pick")) is _Method
        assert type(server.resolve("/M1/Components/Gripper")) is _Component
        assert server.resolve("/Users/operator").name == "operator"


@pytest.mark.asyncio
async def test_check_keeps_the_machinery_item_as_parent(server: Server):
    machine = Machine(name="M1")
    async with server:
        await server.add_machine(machine)
        skill = await machine.add_skill(_Skill(name="Pick"))
        check = _FeasibilityCheck()
        await skill.add_feasibility_check(check)

        assert check.parent is machine
        assert check.path == ObjectPath.parse("/M1/SkillSet/Pick/FeasibilityCheck")


# --------------------------------------------------------------------------- naming rules


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["Pick/Place", ""])
async def test_invalid_segments_are_rejected(server: Server, name: str):
    machine = Machine(name="M1")
    async with server:
        await server.add_machine(machine)
        with pytest.raises(ValueError, match="Invalid name"):
            await machine.add_skill(_Skill(name=name))


@pytest.mark.asyncio
async def test_naming_rule_is_only_a_warning(server: Server):
    machine = Machine(name="M1")
    async with server:
        await server.add_machine(machine)
        with capture_logs() as logs:
            skill = await machine.add_skill(_Skill(name="pick_place"))

    assert skill.path == ObjectPath.parse("/M1/SkillSet/pick_place")
    warnings = [log for log in logs if log["event"].startswith("Name breaks the OpenSMI naming rule")]
    assert [log["path"] for log in warnings] == ["/M1/SkillSet/pick_place"]
    assert all("1.0" in log["event"] for log in warnings)


@pytest.mark.asyncio
async def test_usernames_are_exempt_from_the_naming_rule(server: Server):
    with capture_logs() as logs:
        async with server:
            await server.add_machine(Machine(name="M1"))

    assert not [log for log in logs if "/Users/" in str(log.get("path", "")) and "naming rule" in log["event"]]


# --------------------------------------------------------------------------- shared listings


@pytest.mark.asyncio
async def test_variable_in_a_further_container_is_organized_with_an_alias(grouped: _GroupedMachine):
    force = grouped.parameter_set.Force
    await grouped.monitoring.add(force)

    server = grouped.server
    assert server.resolve("/M1/Monitoring/Force") is force
    assert force.path == ObjectPath.parse("/M1/ParameterSet/Force")
    assert force.aliases == frozenset({ObjectPath.parse("/M1/Monitoring/Force")})

    references = await grouped.monitoring.ua_node.get_references(refs=ObjectIds.HierarchicalReferences)
    by_target = {ref.NodeId: ref.ReferenceTypeId for ref in references}
    assert by_target[force.ua_node.nodeid] == ua.NodeId(ua.Int32(ObjectIds.Organizes))


@pytest.mark.asyncio
async def test_alias_must_not_take_an_existing_path(grouped: _GroupedMachine):
    other = UaVariable(5.0, name="Temperature")
    await grouped.parameter_set.add(other)

    with pytest.raises(ValueError, match="already exists"):
        await grouped.monitoring.add(other)


async def _references(node, reference_type: int) -> set[ua.NodeId]:
    references = await node.get_references(refs=reference_type, direction=ua.BrowseDirection.Forward)
    return {ref.NodeId for ref in references}


@pytest.mark.asyncio
async def test_users_belong_to_the_server(server: Server):
    async with server:
        operator = server.resolve("/Users/operator")
        assert server.users["operator"] is operator
        assert operator.path == ObjectPath.parse("/Users/operator")

        objects = server.ua_server.nodes.objects
        assert server.users.ua_node.nodeid in await _references(objects, ObjectIds.Organizes)
        assert operator.ua_node.nodeid in await _references(server.users.ua_node, ObjectIds.HasComponent)
        parents = await operator.ua_node.get_references(
            refs=ObjectIds.HasComponent, direction=ua.BrowseDirection.Inverse
        )
        assert [ref.NodeId for ref in parents] == [server.users.ua_node.nodeid]


@pytest.mark.asyncio
async def test_machines_have_no_users(server: Server):
    async with server:
        machine = await server.add_machine(Machine(name="M1"))

        assert server.resolve_optional("/M1/Users/operator") is None
        assert server.resolve("/Users/operator").aliases == frozenset()
        building_blocks = await machine.ua_node.get_child(
            f"{server.ua_get_namespace_index(MachineryNodeIds)}:MachineryBuildingBlocks"
        )
        names = [(await child.read_browse_name()).Name for child in await building_blocks.get_children()]
        assert "Users" not in names


@pytest.mark.asyncio
async def test_users_is_a_reserved_machine_name(server: Server):
    async with server:
        with pytest.raises(ValueError, match="reserved"):
            await server.add_machine(Machine(name="Users"))


# --------------------------------------------------------------------------- parent guard


@pytest.mark.asyncio
async def test_parent_cannot_change_after_the_node(server: Server):
    first, second = Machine(name="M1"), Machine(name="M2")
    async with server:
        await server.add_machine(first)
        await server.add_machine(second)
        component = await first.add_component(_Component(name="Gripper"))

        with pytest.raises(RuntimeError, match="already registered"):
            component.parent = second
