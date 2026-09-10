# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from enum import Enum, IntEnum

import structlog
from asyncua import ua
from asyncua.common.node import Node
from asyncua.ua import NodeId, UaError, VariantType
from asyncua.ua.status_codes import StatusCodes as UaStatusCodes
from asyncua.ua.uaerrors import BadNoMatch
from transitions import MachineError, State
from transitions.extensions.asyncio import AsyncMachine, AsyncState, AsyncTransition

from ._server import get_server
from .protocols import HasLocalizedText, UserProtocol


class _UaTypes:
    state_type = ua.FourByteNodeId(ua.ObjectIds.StateType)  # type: ignore
    initial_state_type = ua.FourByteNodeId(ua.ObjectIds.InitialStateType)  # type: ignore
    transition_type = ua.FourByteNodeId(ua.ObjectIds.TransitionType)  # type: ignore
    ref_from_state = ua.FourByteNodeId(ua.ObjectIds.ToState)  # type: ignore
    ref_to_state = ua.FourByteNodeId(ua.ObjectIds.FromState)  # type: ignore


class UaFiniteStateMachine:
    """Base class for all finite state machine OPC-UA representations based on transitions library."""

    ua_node: Node
    """OPC UA node 'StateMachine' representing this instance."""
    ua_node_type_definition: Node | None
    ua_state_variable: Node
    """OPC UA node 'CurrentState' of the state machine."""
    ua_state_id: Node | None = None
    """OPC UA node 'Id' property of the 'CurrentState' node."""
    ua_last_transition_id: Node
    ua_node_available_states: Node | None = None
    """OPC UA node 'AvailableStates'."""
    ua_node_available_transitions: Node | None = None
    """OPC UA node 'AvailableTransitions'."""

    def __init__(
        self,
        name: str,
        states: Sequence[Enum],
        initial: AsyncState | Enum,
        represent_transitions: bool = False,
        represent_states: bool = False,
        exclude_states: Sequence[Enum] | None = None,
    ):
        self.represent_transitions = represent_transitions
        self.represent_states = represent_states
        self.name = name
        self.logger = structlog.getLogger("opensmi.UaFiniteStateMachine", name=name)
        self.state_number = 0
        self.transition_number = 0
        self.before_state: AsyncState | None = None  # keeps track of state before a transition is taken
        self._internal_machine = AsyncMachine(
            model=self,
            name=name,
            states=states,
            initial=initial,
            auto_transitions=False,
            after_state_change=self._after_state_change,
            before_state_change=self._before_state_change,
        )
        # Note: do not use set on_exception parameter of the AsyncMachine constructor, exceptions are required for
        # normal operation!
        self.exclude_states = exclude_states

        self.ua_available_state_node_ids: dict[str, NodeId] = {}
        """Maps the state name to an OPC UA node ID representing the state in the OPC UA address space."""
        self.ua_available_transitions_node_ids: dict[str, NodeId] = {}
        """Maps the transition name to an OPC UA node ID representing the transition in the OPC UA address space."""

    def add_transition(self, **kwargs) -> None:
        """Add the given transition to the internal state machine.

        For documentation of available keyword arguments, please look into `transitions.Machine.add_transition()`.
        """
        self._internal_machine.add_transition(**kwargs)

    async def init(self, state_machine_node: Node, state_machine_type: Node | NodeId | None = None) -> None:
        """Asynchronous initialization of the instance.

        Adds a new OPC-UA object to the given parent node of some
        OPC-UA StateMachineType. Adds sub OPC-UA nodes representing all states of the internal state machine.

        :param state_machine_node: OPC-UA node of the state machine (instantiated from respective types).
        :param state_machine_type: #TODO OPC UA node of the state machine type definition; normally not needed since
                                   node has method 'read_type_definition()', parameter used as workaround if reason
                                   is expected, None is used as default cause lock is no opc ua state machine
        """
        self.ua_node = state_machine_node
        if isinstance(state_machine_type, NodeId):
            state_machine_type = get_server().ua_server.get_node(state_machine_type)
        self.ua_node_type_definition = state_machine_type

        try:
            # self.ua_state_variable = await self.ua_node.get_child(f"{UaTypes.ns_sfm}:CurrentState")
            self.ua_state_variable = await self.ua_node.get_child("CurrentState")
            self.ua_state_id = await self.ua_state_variable.get_child("Id")
        except BadNoMatch:
            self.ua_state_variable = await self.ua_node.add_variable(1, "CurrentState", ua.LocalizedText("", "en-US"))

        with contextlib.suppress(ua.uaerrors.BadNoMatch):
            self.ua_node_available_states = await self.ua_node.get_child("AvailableStates")
            self.ua_node_available_transitions = await self.ua_node.get_child("AvailableTransitions")

        # self.ua_last_transition_id = await asyncio.gather(
        #     self.ua_node.get_child(["0:LastTransition", "0:Id"])
        # )

        if self.represent_states:
            await self._init_ua_states()
        if self.represent_transitions:
            await self._init_ua_transitions()

        await self._add_available_states_transitions()
        await self._set_ua_state(self.current_state)

    async def enable_historizing(self, count: int = 1000) -> None:
        """Enable historizing of state and last transition OPC-UA variables.

        :param count: how many changes should be stored in history
        """
        # TODO waiting for node set
        # nodes = [await self.ua_state_variable.get_child("0:Id")]
        try:
            await get_server().ua_server.historize_node_data_change(self.ua_state_variable, count=count)
        except UaError as err:
            self.logger.warning("Could not historize nodes", error=err)

    async def _init_ua_states(self) -> None:
        """Add all states to OPC-UA state machine representation and sets the initial state of the FSM."""
        self.logger.debug("Initializing UA states...")
        for state in self._internal_machine.states.values():
            await self._init_state(state)

        # needs to be done *after* state initialization
        await self._set_ua_state(self._internal_machine.initial)

    async def _init_ua_transitions(self) -> None:
        """Add all transitions to OPC-UA state machine representation."""
        self.logger.debug("Initializing UA transitions...")
        for event in self._internal_machine.events.values():
            for transitions in event.transitions.values():
                for transition in transitions:
                    await self._init_transition(transition)

    # def _get_node_id(self, state_name: str) -> ua.NodeId:
    #     for name, state in self._internal_machine.states.items():
    #         if name == state_name:
    #             return state.ua_node_id
    #
    #     raise RuntimeError(f"Could not find state named '{state_name}'!")

    # async def _set_ua_state_id(self, new_state: AsyncState):
    #     if self.ua_state_id is None:
    #         return
    #
    #     state_name = _get_proper_state_name(new_state)
    #     try:
    #         await self.ua_state_id.write_value(self.ua_available_state_node_ids[state_name])
    #     except KeyError:
    #         self.logger.warning("Could not find state in available states, state ID not written!",
    #                             state=state_name)

    async def _set_ua_state(self, new_state: HasLocalizedText | AsyncState) -> None:
        if isinstance(new_state, AsyncState):
            new_state = new_state.value  # type: ignore

        old_state = self.before_state.name if self.before_state is not None else None
        self.logger.info("Updating state in OPC UA", new_state=new_state, old_state=old_state)

        # await self._set_ua_state_id(new_state) # TODO?
        await self.ua_state_variable.write_value(new_state.localized_text)  # pyright: ignore[reportAttributeAccessIssue]
        # await self._set_last_transition(new_state)

    async def _init_state(self, state: State, parent_node: Node | None = None) -> None:
        """Add ua_node_id attribute to given state.

        This is achieved by adding a OPC-UA node representing the given state to the OPC-UA representation
        of the state machine.
        """
        if not hasattr(state, "ua_node_id"):  # prevents multiple initializations
            parent = parent_node if parent_node is not None else self.ua_node
            type_ = _UaTypes.initial_state_type if self._internal_machine.initial == state.name else _UaTypes.state_type
            ua_state_node = await parent.add_object(self.ns_idx, state.name, type_)  # type: ignore

            # use enum number if possible
            number = state._name.value if isinstance(state._name, IntEnum) else self.state_number  # noqa: SLF001
            state_number_node = await ua_state_node.get_child("0:StateNumber")
            await state_number_node.write_value(number, varianttype=ua.VariantType.UInt32)
            self.state_number += 1

            state.ua_node_id = ua_state_node.nodeid  # type: ignore

    async def _init_transition(self, transition: AsyncTransition) -> None:
        name = f"{transition.source}To{transition.dest}"
        ua_transition_node = await self.ua_node.add_object(1, name, _UaTypes.transition_type)
        ua_transition_number = await ua_transition_node.get_child("0:TransitionNumber")
        await ua_transition_number.write_value(self.transition_number, VariantType.UInt32)
        self.transition_number += 1
        ua_source = self._internal_machine.states[transition.source].ua_node_id  # type: ignore
        ua_destination = self._internal_machine.states[transition.dest].ua_node_id  # type: ignore
        (await ua_transition_node.add_reference(ua_source, _UaTypes.ref_from_state),)  # type: ignore
        await ua_transition_node.add_reference(ua_destination, _UaTypes.ref_to_state)  # type: ignore

        # TODO add HasCause

    # async def _set_last_transition(self, new_state: State):
    #     if self.represent_transitions and self.before_state is not None:
    #         transition_name = f"{self.before_state.name}To{new_state.name}"
    #         try:
    #             ua_transition_node = await self.ua_node.get_child(f"{self.ns_idx}:{transition_name}")
    #             await self.ua_last_transition_id.write_value(ua_transition_node.nodeid)
    #         except ua.UaError:
    #             self.logger.warning("Could not set LastTransition!", transition=transition_name)

    async def _add_available_states_transitions(self) -> None:

        # TODO: verify if we want to use available states and transitions
        if self.ua_node_type_definition is None:
            return

        for child in await self.ua_node_type_definition.get_children():
            if await child.read_type_definition() in [_UaTypes.state_type, _UaTypes.transition_type]:
                if await child.read_type_definition() == _UaTypes.state_type and (
                    self.exclude_states is None
                    or not any([x.name == (await child.read_display_name()).Text for x in self.exclude_states])
                ):
                    self.ua_available_state_node_ids[(await child.read_browse_name()).Name] = child.nodeid
                elif await child.read_type_definition() == _UaTypes.transition_type and (
                    self.exclude_states is None
                    or not any([x.name in (await child.read_display_name()).Text for x in self.exclude_states])
                ):
                    self.ua_available_transitions_node_ids[(await child.read_browse_name()).Name] = child.nodeid
                else:
                    type_ = "state" if await child.read_type_definition() == _UaTypes.state_type else "transition"
                    self.logger.debug("Excluded node", type=type_, child_name=await child.read_display_name())

        if self.ua_node_available_states is not None:
            await self.ua_node_available_states.write_value(list(self.ua_available_state_node_ids.values()))
        if self.ua_node_available_transitions is not None:
            await self.ua_node_available_transitions.write_value(list(self.ua_available_transitions_node_ids.values()))

    @property
    def current_state(self) -> AsyncState:
        return self._internal_machine.get_state(self._internal_machine.model.state)  # type: ignore

    def _before_state_change(self, *args, **kwargs) -> None:
        """Handle callback from state machine before a state changes. Used to update the OPC-UA representation."""
        self.before_state = self.current_state

    async def _after_state_change(self, *args, **kwargs) -> None:
        """Handle callback from state machine after a state changes. Used to update the OPC-UA representation."""
        await self._set_ua_state(self.current_state)

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
