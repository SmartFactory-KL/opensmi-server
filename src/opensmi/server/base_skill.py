# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Abstract base skill **without** state machine logic."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from asyncua import ua
from asyncua.common.node import Node
from asyncua.ua import QualifiedName
from asyncua.ua.status_codes import StatusCodes as UaStatusCodes
from asyncua.ua.uaerrors import BadNoMatch
from opensmi.core import SkillState
from opensmi.core.errors import OpenSmiRuntimeError, SkillHaltedError
from opensmi.core.ua_node_util import get_child_without_ns, get_node_id
from typing_extensions import override

from opensmi.server.base_callable import BaseCallable
from opensmi.server.interfaces import AbstractSkill
from opensmi.server.nodesets import SmartFactoryMachineSetNodeIds, SmartFactorySkillSetNodeIds
from opensmi.server.protocols import UserAuthorization


class BaseSkill(BaseCallable, AbstractSkill):
    """Abstract base skill **without** state machine logic.

    For skills with logic in Python, there is `FiniteSkillLogicMixin` and `ContinuousSkillLogicMixin`.

    Derived classes must implement at least the ``reset()``, ``start()`` and ``halt()`` methods for a non-suspendable
    skill. The ``_condition_skill_ready()`` method can be overridden in order to add skill specific conditions
    before the ``start()`` method is allowed to be executed by the state machine.
    """

    _ua_state_machine: Node
    """OPC UA base node of state machine."""
    _ua_current_state: Node
    """OPC UA node if the state variable of the state machine."""

    def __init__(
        self,
        *,
        name: str | None = None,
        suspendable: bool = False,
        minimum_access_level: int | None = None,
        precondition_check: BaseSkill | None = None,
        feasibility_check: BaseSkill | None = None,
        **kwargs,
    ) -> None:
        """*Cooperative* Constructor. Create a new skill instance.

        :param name: name of the skill.
        :param suspendable: whether the skill can be suspended/resumed.
        :param minimum_access_level: Minimum required access level to start the skill (see User for more info).
        :param precondition_check: Optional reference to corresponding separate precondition check for this skill.
        :param feasibility_check: Optional reference to corresponding separate feasibility check for this skill.
        """
        super().__init__(name=name, minimum_access_level=minimum_access_level, **kwargs)

        self._suspendable = suspendable
        self._precondition_check = precondition_check
        self._feasibility_check = feasibility_check
        self._called_skills: list[BaseSkill] = []
        self._current_state = SkillState.HALTED

    @override
    async def _get_sub_node(self) -> Node:
        assert self._ua_node is not None
        return await get_child_without_ns(self._ua_node, display_name="SkillExecution")

    @override
    async def _init(self) -> None:
        ua_sub_node = await self._get_sub_node()  # either SkillExecution, PreconditionCheck or FeasibilityCheck

        ns_skill_set = self.server.ua_get_namespace_index(SmartFactorySkillSetNodeIds)
        ns_machine_set = self.server.ua_get_namespace_index(SmartFactoryMachineSetNodeIds)

        self._ua_state_machine = await ua_sub_node.get_child(QualifiedName("StateMachine", ns_skill_set))
        self._ua_current_state = await self.ua_state_machine.get_child("CurrentState")

        node_start = await self.ua_state_machine.get_child(f"{ns_machine_set}:Start")
        node_halt = await self.ua_state_machine.get_child(f"{ns_machine_set}:Halt")
        node_reset = await self.ua_state_machine.get_child(f"{ns_machine_set}:Reset")

        _session = self.server.ua_server.iserver.isession
        _session.add_method_callback(node_start.nodeid, self._ua_start)
        _session.add_method_callback(node_halt.nodeid, self._ua_halt)
        _session.add_method_callback(node_reset.nodeid, self._ua_reset)

        try:
            node_suspend = await self.ua_state_machine.get_child(f"{ns_machine_set}:Suspend")
        except BadNoMatch:
            node_suspend = None

        if self._suspendable:  # checks are not suspendable at all (?!)
            if node_suspend is not None:
                _session.add_method_callback(node_suspend.nodeid, self._ua_suspend)
            else:
                suspend_node_id = get_node_id(self.ua_state_machine, name="Suspend", ns_idx=ns_machine_set)
                await self.ua_state_machine.add_method(suspend_node_id, f"{ns_machine_set}:Suspend", self._ua_suspend)
        elif node_suspend is not None:
            await node_suspend.delete()  # suspend method must not exist for skills that cannot be suspended

        await super()._init()  # cooperative _init() after we are done with the mandatory stuff

        #####################################

        assert self._ua_node is not None
        _ua_precondition_check = await self._ua_node.get_child(f"{ns_skill_set}:PreconditionCheck")
        if self._precondition_check is None:
            await _ua_precondition_check.delete()
            self.logger.debug("Removed unused PreconditionCheck")
        else:
            raise NotImplementedError  # TODO(CaHa): Implement or remove
            # self._precondition_check.parent = self.parent
            # await self._precondition_check.ua_create_node(_ua_precondition_check)
            # await self._precondition_check.init()

        _ua_feasibility_check = await self._ua_node.get_child(f"{ns_skill_set}:FeasibilityCheck")
        if self._feasibility_check is None:
            # no recursive delete to improve startup speed
            await _ua_feasibility_check.delete()
            self.logger.debug("Removed unused FeasibilityCheck")
        else:
            raise NotImplementedError  # TODO(CaHa): Implement or remove
            # self._feasibility_check.parent = self.parent
            # await self._feasibility_check.ua_create_node(_ua_feasibility_check)
            # await self._feasibility_check.init()

    async def _write_current_state(self, state: SkillState) -> None:
        """Write the given ``state`` without any transition logic checks.

        Does nothing if given ``state`` is already ``current_state``.
        """
        assert isinstance(state, SkillState), f"'{state}' is not of type SkillStates"

        if state == self.current_state and self.is_initialized:
            return

        self.logger.info("Updating current state", new_state=state, old_state=self.current_state)
        self._current_state = state
        await self._ua_current_state.write_value(state.localized_text)

    @property
    @override
    def current_state(self) -> SkillState:
        """Return the current state of the skill."""
        return self._current_state

    @property
    @override
    def ua_state_machine(self) -> Node:
        return self._ua_state_machine

    ######################################
    # FSM Conditions

    # Raise UaStatusCodeError for correct OPC-UA status code answers instead of a misc one

    @override
    async def _condition_is_suspendable(self, _user: UserAuthorization) -> bool:
        if not self._suspendable:
            await self.ua_log_error(f"Denied, skill '{self.full_name}' is not suspendable!")
            raise ua.UaStatusCodeError(UaStatusCodes.BadNotImplemented)
        return True

    @override
    async def wait_for_state(self, state: SkillState, *, timeout: float | None = 60) -> None:
        if self.current_state == state:
            return

        source_state = self.current_state
        self.logger.debug(
            "Waiting for state...", source_state=source_state.name, target_state=state.name, timeout=timeout
        )

        async def _wait():
            while True:
                if self.current_state == state:
                    break
                if source_state != SkillState.HALTED and self.current_state == SkillState.HALTED:
                    msg = f"Skill '{self.path}' halted while waiting for state '{state.name}'!"
                    raise SkillHaltedError(msg)
                await asyncio.sleep(0.1)

        if timeout is not None:
            await asyncio.wait_for(_wait(), timeout)
        else:
            await _wait()

    async def _halt_running_called_skills(self):
        for skill in self._called_skills:
            if skill.current_state == SkillState.RUNNING:
                try:
                    await skill.halt()
                except (Exception, asyncio.CancelledError):
                    self.logger.exception("Could not halt running skill! Skipping!", skill_name=skill.full_name)

    @asynccontextmanager
    async def call_other_finite_skill(
        self,
        skill: BaseSkill,
        *,
        wait_for_completion: bool = True,
        timeout: float | None = 60.0,
        reset_after_completion: bool = True,
    ) -> AsyncGenerator:
        """Call another finite skill from this skill.

        :param skill: the finite skill to call.
        :param wait_for_completion: should this method block until the timeout is reached or the skill is completed?
        :param timeout: timeout in seconds, can be disabled by setting it to None.
        :param reset_after_completion: should the called skill be reset after completion?

        :raises TypeError: if given ``skill`` is not finite.
        :raises asyncio.TimeoutError: if the ``timeout`` is reached.
        """
        if skill is None or not skill.is_finite:
            msg = "Wrong type of skill given, a finite skill is required!"
            raise TypeError(msg)

        logger = self.logger.bind(other_skill=skill.full_name)

        if skill not in getattr(self, "_dependencies", ()):
            logger.warning("Calling other skill that is not in dependencies!")

        if skill not in self._called_skills:  # only add it the first time it is called
            self._called_skills.append(skill)

        match skill.current_state:
            case SkillState.READY:
                pass  # Already in Ready, nothing to do
            case SkillState.COMPLETED:  # Safe to reset
                self.logger.info("Resetting completed skill dependency...", skill=skill.full_name)
                await skill.reset()
                await skill.wait_for_state(SkillState.READY, timeout=timeout)
            case _:  # Potentially unsafe to reset
                msg = f"Other skill in unsupported state '{skill.current_state.name}'!"
                raise OpenSmiRuntimeError(msg)

        logger.debug("Trying to start skill...")
        await skill.start()

        yield

        if not wait_for_completion:
            return  # we are done
        # else:
        logger.debug("Waiting for other skill to be completed.", timeout=timeout)
        await skill.wait_for_state(SkillState.COMPLETED, timeout=timeout)

        if reset_after_completion and skill.is_finite:
            # reset seems not to be suitable for conti skills
            logger.debug("Trying to reset other skill after completion...")
            await skill.reset()  # needs to be reset to be ready again (Skill V3+)
            logger.debug("Waiting for other skill to be ready again.", timeout=timeout)
            await skill.wait_for_state(SkillState.READY, timeout=timeout)

    # TODO(CaHa): asynccontextmanager for continuous skills?
    async def call_other_continuous_skill(
        self,
        skill: BaseSkill,
        *,
        wait_for_running: bool = True,
        timeout: float | None = 10.0,
    ) -> None:
        """Call another continuous skill from this skill.

        :param skill: the continuous skill to call.
        :param wait_for_running: should this method block until the timeout is reached or the skill is running?
        :param timeout: timeout in seconds, can be disabled by setting it to None.

        :raises TypeError: if given ``skill`` is not continuous.
        :raises asyncio.TimeoutError: if the ``timeout`` is reached.
        """
        if skill is None or skill.is_finite:
            msg = "Wrong type of skill given, a continuous skill is required!"
            raise TypeError(msg)

        logger = self.logger.bind(other_skill=skill.full_name)

        if skill not in getattr(self, "_dependencies", ()):
            logger.warning("Calling other skill that is not in dependencies!")

        if skill not in self._called_skills:  # only add it the first time it is called
            self._called_skills.append(skill)

        logger.debug("Trying to start other skill...")
        await skill.start()

        if wait_for_running:
            logger.debug("Waiting for other skill to be running", timeout=timeout)
            await skill.wait_for_state(SkillState.RUNNING, timeout=timeout)
