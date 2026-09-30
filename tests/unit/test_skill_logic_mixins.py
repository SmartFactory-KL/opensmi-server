# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio
from abc import ABC
from collections.abc import Callable
from pathlib import PurePosixPath
from types import SimpleNamespace
from typing import Any, override
from unittest.mock import AsyncMock, MagicMock

import pytest
from asyncua import ua
from asyncua.ua.uaerrors import BadStateNotActive
from opensmi.core import SkillState
from opensmi.core.errors import OpenSmiRuntimeError

from opensmi.server import BaseSkill
from opensmi.server.interfaces import AbstractUaObject
from opensmi.server.mixins.skill_logic_mixins import ContinuousSkillLogicMixin, FiniteSkillLogicMixin
from opensmi.server.nodesets import SmartFactoryMachineSetNodeIds
from opensmi.server.protocols import UserAuthorization
from opensmi.server.ua_object import UaObjectDefinition


class MockUaVariable:
    """Fake UaVariable."""

    def __init__(self, initial_value: int | None = None) -> None:
        self.value = initial_value

    async def read(self) -> int | None:
        return self.value

    async def write(self, value: int) -> None:
        self.value = value


class BaseTestSkill(BaseSkill, ABC):
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.hooks: dict[str, Callable[[], Any]] = {}
        self.errors: list[tuple[str, Any]] = []
        self.gate = {"access": True, "startup": True, "deps": True, "suspendable": True}
        super().__init__(name="test_skill")
        self._ua_current_state = MagicMock(write_value=AsyncMock())
        self._logger = MagicMock()

    @property
    @override
    def is_initialized(self) -> bool:
        return True  # We cannot initialize OPC UA stuff without the server

    @property
    @override
    def path(self) -> PurePosixPath:
        return PurePosixPath("/Machine/TestSkill")

    # --- collaborators normally provided by AbstractSkill / AbstractUaLogger
    @override
    async def ua_log_error(
        self, msg: str, *, severity: int = 999, code: str = "", source: AbstractUaObject | None = None
    ) -> None:
        self.errors.append((msg, code))

    @override
    async def _condition_access_allowed(self, user: UserAuthorization) -> bool:
        return self.gate["access"]

    @override
    async def _condition_startup_completed(self, user: UserAuthorization) -> bool:
        return self.gate["startup"]

    @override
    async def _condition_dependencies_ready(self, _user: UserAuthorization) -> bool:
        return self.gate["deps"]

    @override
    async def _condition_is_suspendable(self, _user: UserAuthorization) -> bool:
        return self.gate["suspendable"]

    # --- skill logic hooks
    async def _hook(self, name: str) -> None:
        self.calls.append(name)
        hook = self.hooks.get(name)
        if hook is not None:
            await hook()

    async def _handle_resetting(self) -> None:
        await self._hook("resetting")

    async def _handle_starting(self) -> None:
        await self._hook("starting")

    async def _handle_running(self) -> None:
        await self._hook("running")

    async def _handle_halting(self) -> None:
        await self._hook("halting")

    async def _handle_suspending(self) -> None:
        await self._hook("suspending")

    async def _handle_completing(self) -> None:
        await self._hook("completing")


class FiniteTestSkill(BaseTestSkill, FiniteSkillLogicMixin):
    @override
    async def _get_definition(self) -> UaObjectDefinition:  # MRO -.-
        return await FiniteSkillLogicMixin._get_definition(self)


class ContinuousTestSkill(BaseTestSkill, ContinuousSkillLogicMixin):
    @override
    async def _get_definition(self) -> UaObjectDefinition:  # MRO -.-
        return await ContinuousSkillLogicMixin._get_definition(self)


###


async def wait_until(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.001)

    try:
        await asyncio.wait_for(_poll(), timeout)
    except asyncio.TimeoutError:
        pytest.fail("Timed out waiting for condition")


def registered_tasks(skill) -> dict[str, asyncio.Task]:
    return skill._SkillLogicMixin__tasks  # name-mangled private registry


def written_states(skill) -> list:
    """Values written to the (mocked) OPC-UA ``CurrentState`` node, in order."""
    return [call.args[0] for call in skill._ua_current_state.write_value.await_args_list]


def texts(*states: SkillState) -> list[ua.LocalizedText]:
    return [state.localized_text for state in states]


async def block_forever() -> None:
    """A function that blocks forever."""
    await asyncio.Event().wait()


############################################################


def test_finite_properties() -> None:
    assert FiniteTestSkill().is_finite is True


@pytest.mark.asyncio
async def test_finite_get_definition() -> None:
    definition = await FiniteTestSkill()._get_definition()
    assert definition.object_type == SmartFactoryMachineSetNodeIds.FiniteSkillType


def test_continuous_properties() -> None:
    assert ContinuousTestSkill().is_finite is False


@pytest.mark.asyncio
async def test_continuous_get_definition() -> None:
    definition = await ContinuousTestSkill()._get_definition()
    assert definition.object_type == SmartFactoryMachineSetNodeIds.ContinuousSkillType


############################################################


@pytest.mark.asyncio
async def test_initial_state_is_halted() -> None:
    finite = FiniteTestSkill()
    assert finite.current_state == SkillState.HALTED
    assert written_states(finite) == []


@pytest.mark.asyncio
async def test_finite_full_lifecycle() -> None:
    finite = FiniteTestSkill()
    await finite.reset()
    await finite.wait_for_state(SkillState.READY)

    assert finite.calls == ["resetting"]
    assert written_states(finite) == texts(SkillState.RESETTING, SkillState.READY)

    await finite.start()
    await finite.wait_for_state(SkillState.COMPLETED)

    assert finite.calls == ["resetting", "starting", "running", "completing"]
    assert written_states(finite) == texts(
        SkillState.RESETTING,
        SkillState.READY,
        SkillState.STARTING,
        SkillState.RUNNING,
        SkillState.COMPLETING,
        SkillState.COMPLETED,
    )
    await wait_until(lambda: not registered_tasks(finite))


@pytest.mark.asyncio
async def test_finite_can_reset_and_rerun_after_completion() -> None:
    finite = FiniteTestSkill()
    finite.final_result_data = SimpleNamespace(SuccessfulExecutionsCount=MockUaVariable())  # pyright: ignore[reportAttributeAccessIssue]

    for expected in (1, 2):
        await finite.reset()
        await finite.wait_for_state(SkillState.READY)
        await finite.start()
        await wait_until(lambda: finite.final_result_data.SuccessfulExecutionsCount.value == expected)  # pyright: ignore[reportAttributeAccessIssue]
        await finite.wait_for_state(SkillState.COMPLETED)


@pytest.mark.asyncio
@pytest.mark.parametrize(("initial", "expected"), [(None, 1), (3, 4)])
async def test_finite_increments_successful_executions(initial, expected) -> None:
    finite = FiniteTestSkill()
    counter = MockUaVariable(initial)
    finite.final_result_data = SimpleNamespace(SuccessfulExecutionsCount=counter)  # pyright: ignore[reportAttributeAccessIssue]

    await finite.reset()
    await finite.wait_for_state(SkillState.READY)
    await finite.start()
    await finite.wait_for_state(SkillState.COMPLETED)

    assert counter.value == expected


@pytest.mark.asyncio
async def test_finite_without_result_data_still_completes() -> None:
    skill = FiniteTestSkill()
    assert not hasattr(skill, "final_result_data")
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.start()
    await skill.wait_for_state(SkillState.COMPLETED)


@pytest.mark.asyncio
async def test_continuous_stays_running() -> None:
    skill = ContinuousTestSkill()
    counter = MockUaVariable()
    skill.final_result_data = SimpleNamespace(SuccessfulExecutionsCount=counter)  # pyright: ignore[reportAttributeAccessIssue]

    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.start()
    await wait_until(lambda: "running" in skill.calls)
    await asyncio.sleep(0.02)

    assert skill.current_state == SkillState.RUNNING
    assert counter.value == 1  # counted when RUNNING is entered
    assert SkillState.COMPLETED.localized_text not in written_states(skill)
    assert SkillState.COMPLETING.localized_text not in written_states(skill)


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_can_be_halted(skill) -> None:
    skill.hooks["running"] = block_forever
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.start()
    await wait_until(lambda: "running" in skill.calls)

    await skill.halt()
    await skill.wait_for_state(SkillState.HALTED)
    await wait_until(lambda: not registered_tasks(skill))


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_halt_cancels_running_task(skill) -> None:
    cancelled = asyncio.Event()

    async def running() -> None:
        try:
            await block_forever()
        except asyncio.CancelledError:
            cancelled.set()
            raise

    skill.hooks["running"] = running
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.start()
    await wait_until(lambda: "running" in skill.calls)

    await skill.halt()
    await skill.wait_for_state(SkillState.HALTED)

    assert cancelled.is_set()
    assert skill.calls.count("halting") == 1
    assert SkillState.COMPLETED.localized_text not in written_states(skill)
    await wait_until(lambda: not registered_tasks(skill))


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_failure_in_halting_handler_still_reaches_halted(skill) -> None:

    async def failing() -> None:
        raise OpenSmiRuntimeError("boom", "E42")

    skill.hooks["halting"] = failing
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.halt()
    await skill.wait_for_state(SkillState.HALTED)

    assert skill.errors == [("boom", "E42")]


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_runtime_error_in_starting_halts_skill(skill) -> None:

    async def failing() -> None:
        raise OpenSmiRuntimeError("boom", "E42")

    skill.hooks["starting"] = failing
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.start()
    await skill.wait_for_state(SkillState.HALTED)

    assert skill.errors == [("boom", "E42")]
    assert "running" not in skill.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_unknown_exception_in_running_is_logged_and_halts(skill) -> None:

    async def failing() -> None:
        raise RuntimeError("kaboom")

    skill.hooks["running"] = failing
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.start()
    await skill.wait_for_state(SkillState.HALTED)

    assert len(skill.errors) == 1
    assert "kaboom" in skill.errors[0][0]
    skill.logger.exception.assert_called()


######################################
# Conditions


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_start_denied_when_dependencies_not_ready(skill) -> None:
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)

    skill.gate["deps"] = False
    await skill.start()
    await asyncio.sleep(0.02)

    assert skill.current_state == SkillState.READY
    assert "starting" not in skill.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_start_denied_when_startup_not_completed(skill) -> None:
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)

    skill.gate["startup"] = False
    await skill.start()
    await asyncio.sleep(0.02)

    assert skill.current_state == SkillState.READY


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_access_denied_blocks_reset(skill) -> None:
    skill.gate["access"] = False
    await skill.reset()
    await asyncio.sleep(0.02)

    assert skill.current_state == SkillState.HALTED
    assert "resetting" not in skill.calls


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_suspend_denied_when_not_suspendable(skill) -> None:
    skill.hooks["running"] = block_forever
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.start()
    await wait_until(lambda: "running" in skill.calls)

    skill.gate["suspendable"] = False
    await skill.suspend()
    await asyncio.sleep(0.02)

    assert skill.current_state == SkillState.RUNNING
    assert "suspending" not in skill.calls

    await skill.halt()
    await skill.wait_for_state(SkillState.HALTED)


######################################
# Suspend / resume


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_suspend_and_resume_does_not_spawn_second_running_task(skill) -> None:
    ticks = 0
    stop = False

    async def running() -> None:
        nonlocal ticks
        while not stop:
            await skill._suspend_point()
            ticks += 1
            await asyncio.sleep(0.001)

    skill.hooks["running"] = running
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.start()
    await wait_until(lambda: ticks > 0)

    await skill.suspend()
    await skill.wait_for_state(SkillState.SUSPENDED)
    ticks_suspended = ticks
    await asyncio.sleep(0.02)
    assert ticks == ticks_suspended  # running handler is parked at the suspend point
    assert skill.calls[-1] == "suspending"

    await skill.start()  # resume
    await skill.wait_for_state(SkillState.RUNNING)
    await wait_until(lambda: ticks > ticks_suspended)
    assert skill.calls.count("running") == 1  # same running task continues
    assert skill.calls.count("starting") == 2

    stop = True


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_suspend_point_is_noop_when_not_suspended(skill) -> None:
    await asyncio.wait_for(skill._suspend_point(), timeout=0.5)
    assert not skill._paused_event.is_set()


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_suspend_point_blocks_until_resumed(skill) -> None:
    skill._resume_event.clear()
    waiter = asyncio.create_task(skill._suspend_point())
    await wait_until(skill._paused_event.is_set)
    assert not waiter.done()

    skill._resume_event.set()
    await asyncio.wait_for(waiter, timeout=0.5)
    assert not skill._paused_event.is_set()  # cleared again after resume


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", [FiniteTestSkill(), ContinuousTestSkill()], ids=["Finite Skill", "Continuous Skill"])
async def test_halt_while_suspended(skill) -> None:

    async def running() -> None:
        while True:
            await skill._suspend_point()
            await asyncio.sleep(0.001)

    skill.hooks["running"] = running
    await skill.reset()
    await skill.wait_for_state(SkillState.READY)
    await skill.start()
    await wait_until(lambda: "running" in skill.calls)
    await skill.suspend()
    await skill.wait_for_state(SkillState.SUSPENDED)

    await skill.halt()
    await skill.wait_for_state(SkillState.HALTED)
    await wait_until(lambda: not registered_tasks(skill))


### as dependency


@pytest.mark.asyncio
async def test_finite_condition_as_dependency_ready() -> None:
    finite = FiniteTestSkill()
    assert finite.current_state == SkillState.HALTED

    with pytest.raises(BadStateNotActive):
        await finite.condition_as_dependency_ready(None)  # pyright: ignore[reportArgumentType]

    await finite.reset()
    await finite.wait_for_state(SkillState.READY)

    await finite.condition_as_dependency_ready(None)  # pyright: ignore[reportArgumentType]

    await finite.start()
    await finite.wait_for_state(SkillState.COMPLETED)

    await finite.condition_as_dependency_ready(None)  # pyright: ignore[reportArgumentType]


@pytest.mark.asyncio
async def test_continuous_condition_as_dependency_ready() -> None:
    continuous = ContinuousTestSkill()
    assert continuous.current_state == SkillState.HALTED

    with pytest.raises(BadStateNotActive):
        await continuous.condition_as_dependency_ready(None)  # pyright: ignore[reportArgumentType]

    await continuous.reset()
    await continuous.wait_for_state(SkillState.READY)

    await continuous.condition_as_dependency_ready(None)  # pyright: ignore[reportArgumentType]

    await continuous.start()
    await continuous.wait_for_state(SkillState.RUNNING)

    await continuous.condition_as_dependency_ready(None)  # pyright: ignore[reportArgumentType]
