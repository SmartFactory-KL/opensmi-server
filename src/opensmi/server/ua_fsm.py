# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Finite state machine with OPC-UA representation based on ``transitions`` library."""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from enum import Enum
from typing import Generic, TypeVar

import structlog
from asyncua import ua
from asyncua.common.node import Node
from asyncua.ua.status_codes import StatusCodes as UaStatusCodes
from transitions import MachineError
from transitions.extensions.asyncio import AsyncMachine

from opensmi.server.protocols import UserProtocol

_StateType = TypeVar("_StateType", bound=Enum)


class UaFiniteStateMachine(Generic[_StateType]):
    """Finite state machine with OPC-UA representation based on ``transitions`` library."""

    ua_node: Node
    """OPC UA node 'StateMachine' representing this instance."""
    ua_state_variable: Node
    """OPC UA node 'CurrentState' of the state machine."""

    def __init__(
        self,
        *,
        name: str,
        states: Iterable[_StateType],
        initial: _StateType,
        write_func: Callable[[_StateType], Awaitable[None]],
    ) -> None:
        """Create a new instance.

        :param name: Name of the state machine.
        :param states: States of the state machine.
        :param initial: Initial state of the state machine.
        :param write_func: Async callback to write the current state.
        """
        self.name = name
        self.logger = structlog.getLogger("opensmi.UaFiniteStateMachine", name=name)
        self._internal_machine = AsyncMachine(
            model=self,
            name=name,
            states=tuple(states),
            initial=initial,
            auto_transitions=False,
            # after_state_change=self._after_state_change,
        )
        # Note: machine.after_state_change triggers too late, so use each state's on_enter callback
        for state in self._internal_machine.states.values():
            state.on_enter.append(self._after_state_change)  # pyright: ignore[reportArgumentType]

        # Note: do not use set on_exception parameter of the AsyncMachine constructor, exceptions are required for
        # normal operation!
        self._write_func = write_func

    def add_transition(self, **kwargs) -> None:
        """Add the given transition to the internal state machine.

        For documentation of available keyword arguments, please look into `transitions.Machine.add_transition()`.
        """
        self._internal_machine.add_transition(**kwargs)

    async def init(self, state_machine_node: Node) -> None:
        """Asynchronous initialization of the instance.

        Adds a new OPC-UA object to the given parent node of some
        OPC-UA StateMachineType. Adds sub OPC-UA nodes representing all states of the internal state machine.

        :param state_machine_node: OPC-UA node of the state machine (instantiated from respective types).
        """
        self.ua_node = state_machine_node
        self.ua_state_variable = await self.ua_node.get_child("CurrentState")
        await self._write_func(self.current_state)

    @property
    def current_state(self) -> _StateType:
        """Return the current state of the state machine."""
        return self._internal_machine.model.state  # pyright: ignore[reportReturnType, reportAttributeAccessIssue]

    async def _after_state_change(self, *args, **kwargs) -> None:
        """Handle callback from state machine after a state changes. Used to update the OPC-UA representation."""
        await self._write_func(self.current_state)

    async def try_trigger(self, user: UserProtocol | None, trigger_func: str) -> ua.StatusCode:
        """Try to trigger an event in the internal state machine.

        Handles expected exceptions due to wrong state and UaStatusCodeError exceptions due to unsatisfied conditions.

        :param user: User which triggers the event.
        :param trigger_func: Name of the trigger function.

        :return: Returns either an OPC-UA status code or what the provided success_func returns.
        """
        try:
            # noinspection PyUnresolvedReferences
            # try to call the trigger function, defined during runtime by transitions library
            self.logger.debug("Trying to trigger...", trigger_func=trigger_func)
            await self.trigger(trigger_func, user)  # type: ignore
            return ua.StatusCode(ua.UInt32(UaStatusCodes.Good))
        except MachineError as e:
            self.logger.warning("State machine error", error=e.value, trigger_func=trigger_func)
            return ua.StatusCode(ua.UInt32(UaStatusCodes.BadInvalidState))
        except ua.UaStatusCodeError as e:
            return ua.StatusCode(e.code)
        except Exception as ex:
            self.logger.exception("Could not trigger!", ex=ex, trigger_func=trigger_func)
            return ua.StatusCode(ua.UInt32(UaStatusCodes.BadInternalError))
