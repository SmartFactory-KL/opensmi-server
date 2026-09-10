# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""State machine logic for both finite & continuous skills."""

from __future__ import annotations

import asyncio
import contextlib
from abc import abstractmethod
from asyncio import Task
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Literal

from asyncua import ua
from asyncua.common.methods import uamethod
from asyncua.ua import NodeId, StatusCode
from asyncua.ua.status_codes import StatusCodes as UaStatusCodes
from open_smi.common.enums import SkillState
from open_smi.common.errors import OpenSmiRuntimeError
from open_smi.common.lifecycle_mixin import lifecycle
from open_smi.common.nodesets import SmartFactoryMachineSetNodeIds
from open_smi.server.base_callable import BaseCallable
from open_smi.server.interfaces import AbstractSkill, AbstractUaLogger
from open_smi.server.protocols import SessionProtocol, UserAuthorization
from open_smi.server.ua_variable import UaVariable
from open_smi.server.ua_variable_containers import FinalResultData
from open_smi.server.user import INTERNAL_USER
from transitions import MachineError
from typing_extensions import override

TRIGGER = Literal[
    "halt",
    "halting_done",
    "reset",
    "resetting_done",
    "start",
    "starting_done",
    "suspend",
    "suspending_done",
    "done_internal",
]
""" All state machine transition triggers for the skill state machine. """


class _SkillLogicMixin(AbstractSkill, AbstractUaLogger):
    """Base class for all python-native skills (with state machine logic)."""

    __tasks: set[Task[None]] | None = None
    """Keeps track of tasks related to skill execution and their name. 
    These tasks will get canceled and emptied before handle_halting() is called."""
    __state: SkillState = SkillState.HALTED

    @property
    def _tasks(self) -> set[Task[None]]:
        tasks = self.__tasks
        if tasks is None:
            tasks = set()
            self.__tasks = tasks
        return tasks

    @property
    @override
    def current_state(self) -> SkillState:
        """Current state of the skill."""
        return self.__state

    async def _write_state(self, new_state: SkillState) -> None:
        """Move to `new_state` and mirror the change onto the OPC-UA node."""
        self.logger.debug("Skill state transition", old_state=self.__state, new_state=new_state)
        self._state = new_state
        await self._machine.set_state(new_state)  # TODO

    @lifecycle(after=BaseCallable.lifecycle_callable)
    async def lifecycle_skill_state_machine(self) -> AsyncGenerator[None]:
        """Initialize the skill state machine."""
        # TODO
        yield
        # no shutdown

    #
    # def _add_transitions(self) -> None:
    #     self._machine.add_transition(trigger="reset", source=["Halted", "Starting", "Suspended", "Completed"],
    #                                  dest="Resetting",
    #                                  conditions=[self._condition_access_allowed, self._condition_reset_allowed],
    #                                  after=self._after_resetting)
    #     self._machine.add_transition(trigger="resetting_done", source="Resetting", dest="Ready")
    #
    #     self._machine.add_transition(trigger="halt", source=["Ready", "Running", "Suspended", "Starting", "Resetting",
    #                                                          "Suspending", "Completed"],
    #                                  dest="Halting", conditions=[self._condition_access_allowed],
    #                                  after=self._after_halting)
    #     self._machine.add_transition(trigger="halting_done", source="Halting", dest="Halted")
    #
    #     self._machine.add_transition(trigger="start", source=["Ready", "Suspended"], dest="Starting",
    #                                  conditions=[self._condition_access_allowed, self._condition_startup_completed,
    #                                              self._condition_dependencies_ready, self._condition_start_allowed],
    #                                  after=self._after_starting)
    #     self._machine.add_transition(trigger="starting_done", source="Starting", dest="Running",
    #                                  after=self._after_running)
    #
    #     self._machine.add_transition(trigger="suspend", source="Running", dest="Suspending",
    #                                  conditions=[self._condition_access_allowed, self._condition_is_suspendable,
    #                                             self._condition_suspend_allowed],
    #                                  after=self._after_suspending)
    #     self._machine.add_transition(trigger="suspending_done", source="Suspending", dest="Suspended")

    async def _do_reset(self, user: UserAuthorization) -> None:
        if self.current_state in (SkillState.RESETTING, SkillState.READY):
            return
        if self.current_state not in (
            SkillState.HALTED,
            SkillState.STARTING,
            SkillState.SUSPENDED,
            SkillState.COMPLETED,
        ):
            raise ua.UaStatusCodeError(UaStatusCodes.BadInvalidState)

        await self._condition_access_allowed(user)
        await self._condition_reset_allowed(user)

        await self._write_state(SkillState.RESETTING)
        self._run_handle_method(handle_method=self._handle_resetting, on_success=self._enter_ready)

    async def _enter_ready(self) -> None:
        await self._write_state(SkillState.READY)

    async def _do_halt(self, user: UserAuthorization) -> None:
        if self.current_state in (SkillState.HALTING, SkillState.HALTED):
            return
        if self.current_state not in (
            SkillState.READY,
            SkillState.RUNNING,
            SkillState.SUSPENDED,
            SkillState.STARTING,
            SkillState.RESETTING,
            SkillState.SUSPENDING,
            SkillState.COMPLETED,
        ):
            raise ua.UaStatusCodeError(UaStatusCodes.BadInvalidState)

        await self._condition_access_allowed(user)

        await self._write_state(SkillState.HALTING)
        self._run_handle_method(
            handle_method=self._handle_halting,
            on_success=self._enter_halted,
            cancel_others=True,
        )

    async def _enter_halted(self) -> None:
        await self._write_state(SkillState.HALTED)

    async def _do_start(self, user: UserAuthorization) -> None:
        if self.current_state == SkillState.RUNNING:
            return
        if self.current_state not in (SkillState.READY, SkillState.SUSPENDED):
            raise ua.UaStatusCodeError(UaStatusCodes.BadInvalidState)

        # guards
        await self._condition_access_allowed(user)
        await self._condition_startup_completed(user)
        await self._condition_dependencies_ready(user)
        await self._condition_start_allowed(user)

        await self._write_state(SkillState.STARTING)
        self._run_handle_method(handle_method=self._handle_starting, on_success=self._enter_running)

    async def _do_suspend(self, user: UserAuthorization) -> None:
        if self.current_state in (SkillState.SUSPENDING, SkillState.SUSPENDED):
            return
        if self.current_state != SkillState.RUNNING:
            raise ua.UaStatusCodeError(UaStatusCodes.BadInvalidState)

        # guards
        await self._condition_access_allowed(user)
        await self._condition_is_suspendable(user)
        await self._condition_suspend_allowed(user)

        await self._write_state(SkillState.SUSPENDING)
        self._run_handle_method(handle_method=self._handle_suspending, on_success=self._enter_suspended)

    async def _enter_suspended(self) -> None:
        await self._write_state(SkillState.SUSPENDED)

    ######################################

    def _run_handle_method(
        self,
        *,
        handle_method: Callable[[], Awaitable],
        on_success: Callable[[], Awaitable] | None,
        on_failure: Callable[[], Awaitable] | None = None,
        cancel_others: bool = False,
    ) -> None:
        """Run ``handle_method`` as a tracked background task."""
        self.logger.debug(
            "_wrap_handle_method called",
            handle_method_name=handle_method.__name__,
            on_success=on_success,
            on_failure=on_failure,
            cancel_others=cancel_others,
        )
        if on_failure is None:
            on_failure = self._enter_halted

        if cancel_others:
            for task in self._tasks:
                if not task.cancelled() and not task.done():
                    self.logger.debug("Canceling task...", task_name=task.get_name())
                    task.cancel()
            self._tasks.clear()
        # else:
        #     with contextlib.suppress(ValueError):
        #         await asyncio.wait(self.tasks)  # tasks might be empty and raise a ValueError
        #         self.tasks.clear()

        async def _wrapped() -> None:
            try:
                await handle_method()
                if on_success is not None:
                    await on_success()
            except OpenSmiRuntimeError as err:
                await self.ua_log_error(err.msg, code=err.error_code)
                await asyncio.shield(on_failure())
            except asyncio.CancelledError:
                self.logger.debug(
                    "Task was cancelled",
                    handle_method_name=handle_method.__name__,
                )
                with contextlib.suppress(MachineError):  # we might fail due to already halting
                    await asyncio.shield(on_failure())
            except Exception as ex:
                self.logger.exception(
                    "Caught unhandled unknown exception",
                    handle_method_name=handle_method.__name__,
                )
                await self.ua_log_error(f"Caught unhandled unknown exception: {ex}")
                await asyncio.shield(on_failure())

        def _remove_task(finished_task: Task) -> None:
            with contextlib.suppress(KeyError):
                self._tasks.remove(finished_task)
                self.logger.debug("Removed finished task", task_name=finished_task.get_name())

        task = asyncio.create_task(_wrapped(), name=f"Task_{self.name}_{handle_method.__name__}")
        task.add_done_callback(_remove_task)  # remove task when done
        self._tasks.add(task)

    ######################################
    # skill logic callbacks from the state machine

    async def _handle_resetting(self) -> None:
        """Handle user-specific logic during `SkillState.Resetting` state.

        After this function is done, the skill will automatically advance to the `SkillState.Ready` state.
        """

    async def _handle_starting(self) -> None:
        """Handle user-specific logic during the `SkillState.Starting` state.

        After this function is done, the skill will automatically advance to the `SkillState.Running` state.
        """

    @abstractmethod
    async def _handle_running(self) -> None:
        """Handle user-specific logic during the `SkillState.Running` state.

        After this function is done, the skill will automatically advance to the `SkillState.Completed` state
        if it is finite. Behavior of continuous skills can be configured. By default, they go to
        `SkillState.Halting` state, but if desired can stay in `SkillState.Running`.

        **Note**: This continues to be executed during `SkillState.Suspending` and `SkillState.Suspended` states.
        If your skill supports suspending, you have to check in what state the skill is!
        """
        raise NotImplementedError

    async def _handle_halting(self) -> None:
        """Handle user-specific logic during the `SkillState.Halting` state.

        After this function is done, the skill will automatically advance to the `SkillState.Halted` state.
        """

    async def _handle_suspending(self) -> None:
        """Handle user-specific logic during the `SkillState.Suspending` state.

        After this function is returns, the skill will automatically advance to the `SkillState.Suspended` state.
        """

    ######################################
    # OPC-UA Callbacks

    async def _as_status_code(self, action: Awaitable[None]) -> StatusCode:
        """Run given ``action``, converting a raised ``StatusCodeError`` into its `StatusCode`."""
        try:
            await action
        except ua.UaStatusCodeError as err:
            return StatusCode(err.code)
        return ua.StatusCode(ua.UInt32(UaStatusCodes.Good))

    @uamethod
    @override
    async def _ua_start(self, _parent: NodeId, session: SessionProtocol) -> StatusCode:
        self.logger.debug("Start method called", username=session.user.name)
        return await self._machine.try_trigger(session.user, "start")

    @uamethod
    @override
    async def _ua_suspend(self, _parent: NodeId, session: SessionProtocol) -> StatusCode:
        self.logger.debug("Suspend method called", username=session.user.name)
        return await self._machine.try_trigger(session.user, "suspend")

    @uamethod
    @override
    async def _ua_reset(self, _parent: NodeId, session: SessionProtocol) -> StatusCode:
        self.logger.debug("Reset method called", username=session.user.name)
        return await self._machine.try_trigger(session.user, "reset")

    @uamethod
    @override
    async def _ua_halt(self, _parent: NodeId, session: SessionProtocol) -> StatusCode:
        self.logger.debug("Halt method called", username=session.user.name)
        return await self._machine.try_trigger(session.user, "halt")

    ######################################
    # Internal skill interface (for composite skills)

    @override
    async def start(self) -> None:
        await self._do_start(INTERNAL_USER)

    @override
    async def suspend(self) -> None:
        await self._machine.trigger("suspend", INTERNAL_USER)  # type: ignore

    @override
    async def reset(self) -> None:
        await self._machine.trigger("reset", INTERNAL_USER)  # type: ignore

    @override
    async def halt(self) -> None:
        await self._machine.trigger("halt", INTERNAL_USER)  # type: ignore

    async def _condition_start_allowed(self, _user: UserAuthorization) -> bool:
        """Implement additional custom conditions checked before starting the skill.

        :return: Always ``True``, else an exception is raised.
        :raises UaStatusCodeError: If the skill is not allowed to start.
        """
        return True

    async def _condition_reset_allowed(self, _user: UserAuthorization) -> bool:
        """Implement additional custom conditions checked before resetting the skill.

        :return: Always ``True``, else an exception is raised.
        :raises UaStatusCodeError: If the skill is not allowed to reset.
        """
        return True

    async def _condition_suspend_allowed(self, _user: UserAuthorization) -> bool:
        """Implement additional custom conditions checked before suspending the skill.

        :return: Always ``True``, else an exception is raised.
        :raises UaStatusCodeError: If the skill is not allowed to suspend.
        """
        return True


class BaseSkillFinalResultData(FinalResultData):
    """Base final result data for finite skills."""

    SuccessfulExecutionsCount: UaVariable[int]


class ContinuousSkillLogicMixin(_SkillLogicMixin):
    """Mixin for providing continuous skill logic."""

    def __init__(
        self,
        *,
        handle_running_success_trigger: TRIGGER | None = "halt",  # TODO(CaHa): rename, hide TRIGGER stuff
        **kwargs,
    ) -> None:
        """*Cooperative* Constructor."""
        super().__init__(**kwargs)
        self._handle_running_success_trigger: TRIGGER | None = handle_running_success_trigger
        """ Defines which state machine transition trigger should be called after the _handle_running() returns. """

    @override
    async def _ua_get_type(self) -> NodeId:
        return self.server.ua_get_node_id(SmartFactoryMachineSetNodeIds.ContinuousSkillType)

    @override
    async def _after_running(self, *args, **kwargs):
        """Is called by the finite state machine after `SkillState.Running` is entered."""
        with contextlib.suppress(AttributeError):
            old_value = await self.final_result_data.SuccessfulExecutionsCount.read()  # pyright: ignore[reportAttributeAccessIssue]
            new_value = old_value + 1 if old_value is not None else 1
            await self.final_result_data.SuccessfulExecutionsCount.write(new_value)  # pyright: ignore[reportAttributeAccessIssue]

        await self._wrap_handle_method(
            handle_method=self._handle_running,
            success_trigger=self._handle_running_success_trigger,
        )

    @property
    @override
    def is_finite(self) -> bool:
        return False

    @override
    async def condition_as_dependency_ready(self, _user: UserAuthorization) -> bool:
        if not self.current_state not in [SkillState.READY, SkillState.RUNNING]:
            msg = f"Denied, required continuous skill '{self.path}' is neither ready nor running!"
            await self.ua_log_error(msg)
            raise ua.UaStatusCodeError(UaStatusCodes.BadStateNotActive)

        return True


class FiniteSkillLogicMixin(_SkillLogicMixin):
    """Mixin for providing finite skill logic."""

    @override
    async def _ua_get_type(self) -> NodeId:
        return self.server.ua_get_node_id(SmartFactoryMachineSetNodeIds.FiniteSkillType)

    @override
    def _add_transitions(self) -> None:
        super()._add_transitions()

        # the only difference between finite and continuous skills is that finite skills have the completed state.
        # hence, we only need to add missing transitions
        self._machine.add_transition(
            trigger="done_internal", source="Running", dest="Completing", after=self._after_completing
        )
        self._machine.add_transition(trigger="done_internal", source="Completing", dest="Completed")

    @override
    async def _after_running(self, *args, **kwargs):
        """Is called by the finite state machine after the `SkillState.Running` state is entered."""
        await self._wrap_handle_method(handle_method=self._handle_running, success_trigger="done_internal")

    async def _after_completing(self, *args, **kwargs):
        """Is called by the finite state machine after the `SkillState.Completed` state is entered."""
        # Update successful executions count if it exists
        with contextlib.suppress(AttributeError):
            old_value: int = await self.final_result_data.SuccessfulExecutionsCount.read()  # pyright: ignore[reportAttributeAccessIssue]
            new_value = old_value + 1 if old_value is not None else 1
            await self.final_result_data.SuccessfulExecutionsCount.write(new_value)  # pyright: ignore[reportAttributeAccessIssue]
            self.logger.debug("Updated successful executions count", new_value=new_value)

        await self._wrap_handle_method(handle_method=self._handle_completing, success_trigger="done_internal")

    async def _handle_completing(self) -> None:
        """Handle user-specific logic during the `SkillState.Completing` state.

        After this function is done, the skill will automatically advance to the `SkillState.Completed` state.
        """

    @property
    @override
    def is_finite(self) -> bool:
        return True

    @override
    async def condition_as_dependency_ready(self, _user: UserAuthorization) -> bool:
        if not self.current_state not in [SkillState.READY, SkillState.COMPLETED]:
            msg = f"Denied, required finite skill '{self.full_name}' is neither ready nor completed!"
            await self.ua_log_error(msg)
            raise ua.UaStatusCodeError(UaStatusCodes.BadStateNotActive)

        return True
