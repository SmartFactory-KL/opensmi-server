# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import pytest
from asyncua import ua
from asyncua.client.client import Client
from asyncua.ua.uaerrors import BadInvalidState, BadOutOfRange, BadRequiresLock, BadUserAccessDenied
from opensmi.core import SkillState, Unit
from opensmi.core.skill_state_machine import state_node_id, states, transitions
from typing_extensions import override

from opensmi.server import (
    BaseMachine,
    BaseSkillFinalResultData,
    BaseSkillFinite,
    ParameterSet,
    SkillSet,
    UaVariable,
)
from opensmi.server.mixins import FinalResultDataMixin, ParameterSetMixin, SkillSetMixin


class _SkillParameterSet(ParameterSet):
    x = UaVariable(0, unit=Unit.NANOAMPERE, range=(0, 10))
    y = UaVariable(0, unit="nA", range=(0, 10))


class _SkillFinalResultData(BaseSkillFinalResultData):
    ComputationResult = UaVariable(0, unit="nA", range=(0, 20))


class TestSkill(
    ParameterSetMixin[_SkillParameterSet],
    FinalResultDataMixin[_SkillFinalResultData],
    BaseSkillFinite,
):
    __test__ = False

    @override
    async def _handle_running(self) -> None:
        x = await self.parameter_set.x.read()
        y = await self.parameter_set.y.read()
        await self.final_result_data.ComputationResult.write(x + y)


class _SkillSet(SkillSet):
    TestSkill: TestSkill


class _TestMachine(
    SkillSetMixin[_SkillSet],
    BaseMachine,
):
    __test__ = False

    async def _init(self) -> None:
        await super()._init()

        await self.add_skill(TestSkill())

    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )


@pytest.mark.asyncio
async def test_skill_basic_interaction_via_asyncua_client(server) -> None:
    machine = _TestMachine()

    async with server:
        await server.add_machine(machine)
        skill = machine.skill_set.TestSkill

        await server.start(blocking=False)

        client = Client(url=f"opc.tcp://operator:operator@localhost:{server.ua_server.bserver.port}")
        async with client:
            client_lock_node = client.get_node(machine.lock.ua_node.nodeid)
            client_skill_fsm = client.get_node(skill.ua_state_machine.nodeid)
            client_skill_fsm_current_state = client.get_node(skill._machine.ua_state_variable.nodeid)
            client_skill_param_x = client.get_node(skill.parameter_set.x.ua_node.nodeid)
            client_skill_param_y = client.get_node(skill.parameter_set.y.ua_node.nodeid)
            client_skill_result = client.get_node(skill.final_result_data.ComputationResult.ua_node.nodeid)

            assert skill.current_state == SkillState.HALTED

            with pytest.raises(BadRequiresLock):
                await client_skill_fsm.call_method("6:Reset")

            assert skill.current_state == SkillState.HALTED

            with pytest.raises(BadInvalidState):  # TODO(CaHa): should be BadRequiresLock
                await client_skill_fsm.call_method("6:Start")

            assert skill.current_state == SkillState.HALTED

            with pytest.raises(BadInvalidState):  # TODO(CaHa): should be BadRequiresLock
                await client_skill_fsm.call_method("6:Halt")

            assert skill.current_state == SkillState.HALTED

            with pytest.raises(BadUserAccessDenied):  # TODO(CaHa): should be BadRequiresLock
                await client_skill_param_x.write_value(5)

            await client_lock_node.call_method("2:InitLock")
            assert machine.lock.locking_user
            assert machine.lock.locking_user.name == "operator"

            assert skill.current_state == SkillState.HALTED

            with pytest.raises(BadInvalidState):
                await client_skill_fsm.call_method("6:Start")

            assert skill.current_state == SkillState.HALTED

            await client_skill_fsm.call_method("6:Reset")
            await skill.wait_for_state(SkillState.READY)
            assert await client_skill_fsm_current_state.read_value() == SkillState.READY.localized_text

            with pytest.raises(BadOutOfRange):
                await client_skill_param_x.write_value(11)
            await client_skill_param_x.write_value(3)

            with pytest.raises(BadOutOfRange):
                await client_skill_param_y.write_value(11)
            await client_skill_param_y.write_value(5)

            await client_skill_fsm.call_method("6:Start")
            await skill.wait_for_state(SkillState.COMPLETED)
            assert await client_skill_fsm_current_state.read_value() == SkillState.COMPLETED.localized_text

            assert await client_skill_result.read_value() == 3 + 5

            await client_skill_fsm.call_method("6:Halt")
            await skill.wait_for_state(SkillState.HALTED)
            assert await client_skill_fsm_current_state.read_value() == SkillState.HALTED.localized_text


@pytest.mark.asyncio
async def test_skill_state_machine_follows_the_spec(server) -> None:
    machine = _TestMachine()

    async with server:
        await server.add_machine(machine)
        skill = machine.skill_set.TestSkill
        await server.start(blocking=False)

        client = Client(url=f"opc.tcp://operator:operator@localhost:{server.ua_server.bserver.port}")
        async with client:
            fsm = client.get_node(skill.ua_state_machine.nodeid)

            async def read(*path: str) -> object:
                return await (await fsm.get_child(list(path))).read_value()

            # TestSkill is finite and not suspendable
            assert await read("AvailableStates") == [
                server.ua_get_node_id(state_node_id(state)) for state in states(finite=True, suspendable=False)
            ]
            assert await read("AvailableTransitions") == [
                server.ua_get_node_id(t.node_id) for t in transitions(finite=True, suspendable=False)
            ]
            assert await read("CurrentState", "Id") == server.ua_get_node_id(state_node_id(SkillState.HALTED))
            for path in (["LastTransition"], ["LastTransition", "Id"], ["CurrentState", "Number"]):
                assert (await (await fsm.get_child(path)).read_display_name()).Text == path[-1]

            await skill.reset()
            await skill.wait_for_state(SkillState.READY)

            assert await read("CurrentState") == SkillState.READY.localized_text
            assert await read("CurrentState", "Id") == server.ua_get_node_id(state_node_id(SkillState.READY))
            assert await read("CurrentState", "Number") == SkillState.READY.id
            assert await read("LastTransition") == ua.LocalizedText("ResettingToReady")
            resetting_to_ready = next(
                t for t in transitions(finite=True, suspendable=False) if t.name == "ResettingToReady"
            )
            assert await read("LastTransition", "Id") == server.ua_get_node_id(resetting_to_ready.node_id)
