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
from opensmi.core import SkillState
from opensmi.core.errors import OpenSmiRuntimeError
from opensmi.core.lifecycle_mixin import lifecycle
from transitions import MachineError
from typing_extensions import override

from opensmi.server.base_callable import BaseCallable
from opensmi.server.interfaces import AbstractSkill, AbstractUaLogger
from opensmi.server.nodesets import SmartFactoryMachineSetNodeIds
from opensmi.server.protocols import SessionProtocol, UserAuthorization
from opensmi.server.ua_fsm import UaFiniteStateMachine
from opensmi.server.ua_object import UaObjectDefinition
from opensmi.server.ua_variable import UaVariable
from opensmi.server.ua_variable_containers import FinalResultData
from opensmi.server.user import INTERNAL_USER

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

    def __init__(self, **kwargs) -> None:  # TODO(CaHa): proper mixin without __init__
        """*Cooperative* constructor."""
        super().__init__(**kwargs)

        self.__tasks: dict[str, Task] = {}
        """Keeps track of tasks related to skill execution and their name. 
        These tasks will get canceled and emptied before handle_halting() is called."""

        self._resume_event: asyncio.Event = asyncio.Event()
        self._resume_event.set()  # not suspended by default
        self._paused_event: asyncio.Event = asyncio.Event()

        self._machine: UaFiniteStateMachine = UaFiniteStateMachine(
            name=f"{self.name}_StateMachine",
            states=SkillState,  # type: ignore
            initial=SkillState.HALTED,
            exclude_states=[] if self.is_finite else [SkillState.COMPLETED, SkillState.COMPLETING],
            write_func=self._write_current_state,
        )
        """Skill Finite State Machine."""
        self._add_transitions()

    @lifecycle(after=BaseCallable.lifecycle_callable)
    async def lifecycle_skill_state_machine(self) -> AsyncGenerator[None]:
        """Initialize the skill state machine."""
        # ensure initialization comes first
        await self._machine.init(self.ua_state_machine)
        # self.server.ua_get_node_id(SmartFactoryMachineSetNodeIds.SkillStateMachineType),
        yield
        # no shutdown

    def _add_transitions(self) -> None:
        self._machine.add_transition(
            trigger="reset",
            source=[SkillState.HALTED, SkillState.STARTING, SkillState.SUSPENDED, SkillState.COMPLETED],
            dest=SkillState.RESETTING,
            conditions=[self._condition_access_allowed, self._condition_reset_allowed],
            after=self._after_resetting,
        )
        self._machine.add_transition(trigger="resetting_done", source=SkillState.RESETTING, dest=SkillState.READY)

        self._machine.add_transition(
            trigger="halt",
            source=[
                SkillState.RESETTING,
                SkillState.READY,
                SkillState.STARTING,
                SkillState.RUNNING,
                SkillState.COMPLETED,
                SkillState.SUSPENDED,
                SkillState.SUSPENDING,
            ],
            dest=SkillState.HALTING,
            conditions=[self._condition_access_allowed],
            after=self._after_halting,
        )
        self._machine.add_transition(trigger="halting_done", source=SkillState.HALTING, dest=SkillState.HALTED)

        self._machine.add_transition(
            trigger="start",
            source=[SkillState.READY, SkillState.SUSPENDED],
            dest=SkillState.STARTING,
            conditions=[
                self._condition_access_allowed,
                self._condition_startup_completed,
                self._condition_dependencies_ready,
                self._condition_start_allowed,
            ],
            after=self._after_starting,
        )
        self._machine.add_transition(
            trigger="starting_done", source=SkillState.STARTING, dest=SkillState.RUNNING, after=self._after_running
        )

        self._machine.add_transition(
            trigger="suspend",
            source=SkillState.RUNNING,
            dest=SkillState.SUSPENDING,
            conditions=[
                self._condition_access_allowed,
                self._condition_is_suspendable,
                self._condition_suspend_allowed,
            ],
            after=self._after_suspending,
        )
        self._machine.add_transition(trigger="suspending_done", source=SkillState.SUSPENDING, dest=SkillState.SUSPENDED)

    async def _after_resetting(self, *args, **kwargs) -> None:
        """Is called by the finite state machine after the `SkillState.RESETTING` state is entered."""
        await self._wrap_handle_method(handle_method=self._handle_resetting, success_trigger="resetting_done")

    async def _after_halting(self, *args, **kwargs) -> None:
        """Is called by the finite state machine after the `SkillState.HALTING` state is entered."""
        # prevent infinite loop
        await self._wrap_handle_method(
            handle_method=self._handle_halting,
            success_trigger="halting_done",
            fail_trigger="halting_done",
            cancel_others=True,
        )

    async def _after_starting(self, *args, **kwargs) -> None:
        """Is called by the finite state machine after the `SkillState.STARTING` state is entered."""
        # covers both a fresh start (Ready) and a resume (from Suspended)
        self._resume_event.set()

        await self._wrap_handle_method(handle_method=self._handle_starting, success_trigger="starting_done")

    @abstractmethod
    async def _after_running(self, *args, **kwargs) -> None:
        raise NotImplementedError

    async def _after_suspending(self, *args, **kwargs):
        """Is called by the finite state machine after the `SkillState.SUSPENDING` state is entered."""
        self._resume_event.clear()

        async def _suspend_and_wait_for_pause() -> None:
            await self._handle_suspending()
            await self._paused_event.wait()

        await self._wrap_handle_method(handle_method=_suspend_and_wait_for_pause, success_trigger="suspending_done")

    async def _wrap_handle_method(
        self,
        *,
        handle_method: Callable[[], Awaitable],
        success_trigger: TRIGGER | None,
        fail_trigger: TRIGGER = "halt",
        cancel_others: bool = False,
    ):
        self.logger.debug(
            "_wrap_handle_method called",
            handle_method_name=handle_method.__name__,
            success_trigger=success_trigger,
            fail_trigger=fail_trigger,
            cancel_others=cancel_others,
        )

        if cancel_others:
            for task in self.__tasks.values():
                if not task.cancelled() and not task.done():
                    self.logger.debug("Canceling task...", task_name=task.get_name())
                    task.cancel()
            self.__tasks.clear()
        # else:
        #     with contextlib.suppress(ValueError):
        #         await asyncio.wait(self.tasks)  # tasks might be empty and raise a ValueError
        #         self.tasks.clear()

        async def _wrapped() -> None:
            try:
                await handle_method()
                if success_trigger is not None:
                    await self._machine.trigger(success_trigger, INTERNAL_USER)  # type: ignore
            except OpenSmiRuntimeError as err:
                await self.ua_log_error(err.msg, code=err.error_code)
                await asyncio.shield(self._machine.trigger(fail_trigger, INTERNAL_USER))  # type: ignore
            except asyncio.CancelledError:
                self.logger.debug(
                    "Task was cancelled",
                    handle_method_name=handle_method.__name__,
                )
                with contextlib.suppress(MachineError):  # we might fail due to already halting
                    await asyncio.shield(self._machine.trigger(fail_trigger, INTERNAL_USER))  # type: ignore
            except Exception as ex:
                self.logger.exception(
                    "Caught unhandled unknown exception",
                    handle_method_name=handle_method.__name__,
                )
                await self.ua_log_error(f"Caught unhandled unknown exception: {ex}")
                await asyncio.shield(self._machine.trigger(fail_trigger, INTERNAL_USER))  # type: ignore

        def _remove_task(finished_task: Task):
            with contextlib.suppress(KeyError):
                del self.__tasks[finished_task.get_name()]
                self.logger.debug("Removed finished task", task_name=finished_task.get_name())

        task_name = f"Task_{self.name}_{handle_method.__name__}"
        with contextlib.suppress(KeyError):
            existing_task = self.__tasks[task_name]
            if not existing_task.done() and not existing_task.cancelled():
                self.logger.debug("Task already exists, skipping task creation!", task_name=task_name)
                return

        task = asyncio.create_task(_wrapped(), name=task_name)
        self.__tasks[task_name] = task
        task.add_done_callback(_remove_task)  # remove task when done

    ######################################
    # skill logic callbacks from the state machine

    async def _handle_resetting(self) -> None:
        """Handle user-specific logic during `SkillState.RESETTING` state.

        After this function is done, the skill will automatically advance to the `SkillState.READY` state.
        """

    async def _handle_starting(self) -> None:
        """Handle user-specific logic during the `SkillState.STARTING` state.

        After this function is done, the skill will automatically advance to the `SkillState.RUNNING` state.
        """

    @abstractmethod
    async def _handle_running(self) -> None:
        """Handle user-specific logic during the `SkillState.RUNNING` state.

        After this function is done, the skill will automatically advance to the `SkillState.COMPLETED` state
        if it is finite. Behavior of continuous skills can be configured. By default, they go to
        `SkillState.HALTING` state, but if desired can stay in `SkillState.RUNNING`.

        **Note**: This continues to be executed during `SkillState.SUSPENDING` and `SkillState.SUSPENDED` states.
        If your skill supports suspending, you have to check in what state the skill is!
        """
        raise NotImplementedError

    async def _handle_halting(self) -> None:
        """Handle user-specific logic during the `SkillState.HALTING` state.

        After this function is done, the skill will automatically advance to the `SkillState.HALTED` state.
        """

    async def _handle_suspending(self) -> None:
        """Handle user-specific logic during the `SkillState.SUSPENDING` state.

        After this function is returns, the skill will automatically advance to the `SkillState.SUSPENDED` state.
        """

    async def _suspend_point(self) -> None:
        """Blocks if current_state is `SkillState.SUSPENDING` and allow advance to `SkillState.SUSPENDED`.

        Call from `_handle_running` at any point where it's safe to pause.
        """
        if self._resume_event.is_set():
            return

        self._paused_event.set()
        try:
            self.logger.debug("Paused at checkpoint, awaiting resume...")
            await self._resume_event.wait()
            self.logger.debug("Resumed from checkpoint")
        finally:
            self._paused_event.clear()

    ######################################
    # OPC-UA Callbacks

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
        await self._machine.trigger("start", INTERNAL_USER)  # type: ignore

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

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=SmartFactoryMachineSetNodeIds.ContinuousSkillType,
            instantiate_optional=True,  # TODO(CaHa): instantiate only what is actually needed
        )

    @override
    async def _after_running(self, *args, **kwargs):
        """Is called by the finite state machine after `SkillState.RUNNING` is entered."""
        with contextlib.suppress(AttributeError):
            old_value = await self.final_result_data.SuccessfulExecutionsCount.read()  # pyright: ignore[reportAttributeAccessIssue]
            new_value = old_value + 1 if old_value is not None else 1
            await self.final_result_data.SuccessfulExecutionsCount.write(new_value)  # pyright: ignore[reportAttributeAccessIssue]

        await self._wrap_handle_method(handle_method=self._handle_running, success_trigger=None)

    @property
    @override
    def is_finite(self) -> bool:
        return False

    @override
    async def condition_as_dependency_ready(self, _user: UserAuthorization) -> bool:
        if self.current_state not in [SkillState.READY, SkillState.RUNNING]:
            msg = (
                f"Denied, required continuous skill '{self.path}' is neither ready nor "
                f"running (state={self.current_state})!"
            )
            await self.ua_log_error(msg)
            raise ua.UaStatusCodeError(UaStatusCodes.BadStateNotActive)

        return True


class FiniteSkillLogicMixin(_SkillLogicMixin):
    """Mixin for providing finite skill logic."""

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=SmartFactoryMachineSetNodeIds.FiniteSkillType,
            instantiate_optional=True,  # TODO(CaHa): instantiate only what is actually needed
        )

    @override
    def _add_transitions(self) -> None:
        super()._add_transitions()

        # the only difference between finite and continuous skills is that finite skills have the completed state.
        # hence, we only need to add missing transitions
        self._machine.add_transition(
            trigger="done_internal", source=SkillState.RUNNING, dest=SkillState.COMPLETING, after=self._after_completing
        )
        self._machine.add_transition(trigger="done_internal", source=SkillState.COMPLETING, dest=SkillState.COMPLETED)

    @override
    async def _after_running(self, *args, **kwargs):
        """Is called by the finite state machine after the `SkillState.RUNNING` state is entered."""
        await self._wrap_handle_method(handle_method=self._handle_running, success_trigger="done_internal")

    async def _after_completing(self, *args, **kwargs):
        """Is called by the finite state machine after the `SkillState.COMPLETED` state is entered."""
        # Update successful executions count if it exists
        with contextlib.suppress(AttributeError):
            old_value: int = await self.final_result_data.SuccessfulExecutionsCount.read()  # pyright: ignore[reportAttributeAccessIssue]
            new_value = old_value + 1 if old_value is not None else 1
            await self.final_result_data.SuccessfulExecutionsCount.write(new_value)  # pyright: ignore[reportAttributeAccessIssue]
            self.logger.debug("Updated successful executions count", new_value=new_value)

        await self._wrap_handle_method(handle_method=self._handle_completing, success_trigger="done_internal")

    async def _handle_completing(self) -> None:
        """Handle user-specific logic during the `SkillState.COMPLETING` state.

        After this function is done, the skill will automatically advance to the `SkillState.COMPLETED` state.
        """

    @property
    @override
    def is_finite(self) -> bool:
        return True

    @override
    async def condition_as_dependency_ready(self, _user: UserAuthorization) -> bool:
        if self.current_state not in [SkillState.READY, SkillState.COMPLETED]:
            msg = (
                f"Denied, required finite skill '{self.path}' is neither ready nor completed!"
                f" (state={self.current_state})!"
            )
            await self.ua_log_error(msg)
            raise ua.UaStatusCodeError(UaStatusCodes.BadStateNotActive)

        return True
