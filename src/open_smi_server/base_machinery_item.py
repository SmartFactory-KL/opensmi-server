# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import contextlib
import time
from abc import abstractmethod
from collections.abc import AsyncGenerator, Iterable, Sequence

from asyncua import ua
from asyncua.common.node import Node
from asyncua.ua.status_codes import StatusCodes as UaStatusCodes
from asyncua.ua.uaerrors import BadNoMatch
from typing_extensions import TypeVar, override

from open_smi_common.enums import MachineryItemState, MachineryOperationMode, SkillState
from open_smi_common.errors import OpenSmiRuntimeError
from open_smi_common.lifecycle_mixin import lifecycle
from open_smi_common.nodesets import MachineryNodeIds, SmartFactoryMachineSetNodeIds
from open_smi_common.ua_node_util import write_value
from open_smi_server.common import (
    halt_skills_parallel_and_wait,
    reset_skills_parallel_and_wait,
)
from open_smi_server.interfaces import AbstractMethod, AbstractSkill, AbstractUaLogger
from open_smi_server.mixins.ua_variable_container_mixins import (
    AttributesMixin,
    IdentificationMixin,
    MonitoringMixin,
    ParameterSetMixin,
)
from open_smi_server.protocols import UserAuthorization
from open_smi_server.ua_object import UaObject, UaObjectDefinition
from open_smi_server.ua_object_containers import Components, MethodSet, SkillSet
from open_smi_server.ua_variable_containers import MachineryComponentIdentification

_ComponentType = TypeVar("_ComponentType", bound="BaseMachineryItem")
_SkillType = TypeVar("_SkillType", bound="AbstractSkill")
_MethodType = TypeVar("_MethodType", bound="AbstractMethod")

INITIAL_STATE = MachineryItemState.OUT_OF_SERVICE
INITIAL_OPERATION_MODE = MachineryOperationMode.NONE


class BaseMachineryItem(UaObject, AbstractUaLogger):
    """Base class for both machines (`BaseMachine`) and components (`BaseComponent`)."""

    _ua_machinery_building_blocks: Node | None = None
    _ua_state_machine: Node | None = None
    """Optional 'MachineryItemState' (finite state machine) OPC UA node."""
    _ua_operation_mode: Node | None = None
    """Optional 'MachineryOperationMode' (finite state machine) OPC UA node."""

    _ua_state_machine_current_state: Node | None = None
    """Optional current_state OPC UA node of 'MachineryItemState'."""
    _ua_operation_mode_current_state: Node | None = None
    """Optional current_state OPC UA node of 'MachineryOperationMode'."""

    def __init__(
        self,
        *,
        name: str | None = None,
        minimum_access_level: int | None = None,
        **kwargs,
    ) -> None:
        """*Cooperative* constructor."""
        super().__init__(name=name, minimum_access_level=minimum_access_level, **kwargs)
        self._components: Components = Components()
        self._skill_set: SkillSet = SkillSet()
        self._method_set: MethodSet = MethodSet()

        self._current_state: MachineryItemState = INITIAL_STATE
        self._current_operation_mode: MachineryOperationMode = INITIAL_OPERATION_MODE

    @property
    def components(self) -> Components:
        """(Untyped) subcomponents of this machinery item."""
        return self._components

    async def __init_components(self) -> None:
        self._components.parent = self
        await self._components.ua_create_node(self.ua_node, remove_existing=True)
        await self._components.init()

    async def add_component(self, component: _ComponentType) -> _ComponentType:
        """Add given (sub-)component instance, initializes it and keep track of it."""
        assert isinstance(component, BaseComponent), f"Given component '{component.__class__.__name__}' is invalid!"
        assert not component.is_initialized, f"Given component '{component.__class__.__name__}' is already initialized!"

        if not self._components.is_initialized:
            await self.__init_components()

        start_time = time.monotonic()

        component.parent = self  # pyright: ignore[reportAttributeAccessIssue]
        await component.ua_create_node(self._components.ua_node)
        await component.init()

        self._components[component.name] = component
        self.logger.info("Added sub-component", sub_component=component.name, elapsed=time.monotonic() - start_time)
        return component

    async def _init_status(self) -> None:
        ns = self.server.ua_get_namespace_index(MachineryNodeIds)
        try:
            self._ua_machinery_building_blocks = await self.ua_node.get_child(f"{ns}:MachineryBuildingBlocks")

            self._ua_state_machine = await self._ua_machinery_building_blocks.get_child(f"{ns}:MachineryItemState")
            self._ua_operation_mode = await self._ua_machinery_building_blocks.get_child(f"{ns}:MachineryOperationMode")

            self._ua_state_machine_current_state = await self._ua_state_machine.get_child("CurrentState")
            self._ua_operation_mode_current_state = await self._ua_operation_mode.get_child("CurrentState")

            # Set the current state and operation mode (also in OPC UA)
            await self.set_current_state(INITIAL_STATE)
            await self.set_current_operation_mode(INITIAL_OPERATION_MODE)

        except BadNoMatch:
            self.logger.debug("No machinery state machine found.")

    @property
    def current_state(self) -> MachineryItemState:
        """Return the current machinery item state. Read-only property."""
        return self._current_state

    @property
    def current_operation_mode(self) -> MachineryOperationMode:
        """Return the current machinery operation mode. Read-only property."""
        return self._current_operation_mode

    async def set_current_state(self, state: MachineryItemState) -> None:
        """Set the current state of this machinery item without any transition logic checks."""
        assert isinstance(state, MachineryItemState)
        if self._current_state == state:
            return

        self._current_state = state
        if self._ua_state_machine_current_state is not None:
            await write_value(
                self._ua_state_machine_current_state,
                state.localized_text,
                variant_type=ua.VariantType.LocalizedText,
            )

    async def set_current_operation_mode(self, mode: MachineryOperationMode) -> None:
        """Set the current operation mode of this machinery item without any transition logic checks."""
        assert isinstance(mode, MachineryOperationMode)
        if self._current_operation_mode == mode:
            return

        self._current_operation_mode = mode
        if self._ua_operation_mode_current_state:
            await write_value(
                self._ua_operation_mode_current_state,
                mode.localized_text,
                variant_type=ua.VariantType.LocalizedText,
            )

    #####################################

    async def __init_skill_set(self) -> None:
        """Initialize the skill set `UaObjectContainer`."""
        self.skill_set.parent = self  # pyright: ignore[reportAttributeAccessIssue]
        await self.skill_set.ua_create_node(self.ua_node, exist_ok=True)  # pyright: ignore[reportAttributeAccessIssue]
        await self.skill_set.init()

    @property
    def skill_set(self) -> SkillSet:
        """(Untyped) skill set of this machinery item."""
        return self._skill_set

    async def add_skill(self, skill: _SkillType) -> _SkillType:
        """Add the given skill instance to the asset."""
        from open_smi_server import BaseSkill

        assert isinstance(skill, BaseSkill), f"Given skill {skill} does not inherit BaseSkill!"
        assert skill.name not in self.skill_set, f"Given skill {skill} already added!"

        if not self.skill_set.is_initialized:
            await self.__init_skill_set()

        start_time = time.monotonic()

        if not skill.is_initialized:
            skill.parent = self  # pyright: ignore[reportAttributeAccessIssue]
            await skill.ua_create_node(self.skill_set.ua_node, remove_existing=True)
            await skill.init()

        self.skill_set[skill.name] = skill

        self.logger.info("Added skill", skill=skill.name, elapsed=time.monotonic() - start_time)
        return skill

    async def add_skills(self, skills: Sequence[AbstractSkill]) -> None:
        """Add given sequence of skill instances to the asset."""
        for skill in skills:
            await self.add_skill(skill)

    #####################################

    async def __init_method_set(self) -> None:
        """Initialize the method set `UaObjectContainer`."""
        self.method_set.parent = self  # pyright: ignore[reportAttributeAccessIssue]
        await self.method_set.ua_create_node(self.ua_node, exist_ok=True)  # pyright: ignore[reportAttributeAccessIssue]
        await self.method_set.init()

    @property
    def method_set(self) -> MethodSet:
        """Return untyped method set `UaObjectContainer`. Read-only property."""
        return self._method_set

    async def add_method(self, method: _MethodType) -> _MethodType:
        """Add the given method instance to the asset."""
        from open_smi_server import BaseMethod

        assert isinstance(method, BaseMethod), f"Given method {method} does not inherit BaseMethod!"
        assert method.name not in self.method_set, f"Given method {method} already added!"

        if not self.method_set.is_initialized:
            await self.__init_method_set()

        start_time = time.monotonic()

        if not method.is_initialized:
            method.parent = self  # pyright: ignore[reportAttributeAccessIssue]
            await method.ua_create_node(self.method_set.ua_node)
            await method.init()

        self.method_set[method.name] = method

        self.logger.info("Added method", method=method.name, elapsed=time.monotonic() - start_time)
        return method

    async def add_methods(self, methods: Sequence[AbstractMethod]) -> None:
        """Add given sequence of method instances to the asset."""
        for method in methods:
            await self.add_method(method)

    #####################################
    # FSM Conditions

    # Raise UaStatusCodeError for correct OPC-UA status code answers instead of a misc one

    async def condition_startup_completed(self, _user: UserAuthorization) -> bool:
        """Check if startup was completed.

        Startup is considered completed if either the corresponding startup skill (`BaseStartupSkill`)
         is in `SkillState.RUNNING` or the machinery item has no such skill.
        """
        with contextlib.suppress(KeyError):
            if self.startup_skill.current_state != SkillState.RUNNING:
                await self.ua_log_error(f"Denied, skill '{self.startup_skill.path}' must be running!")
                raise ua.UaStatusCodeError(ua.status_codes.StatusCodes.BadInvalidState)
            # else: has the StartupSkill and it is running
        return True  # has no StartupSkill -> always ready

    async def _condition_component_ready(self, user: UserAuthorization) -> bool:
        return True  # To implement custom checks that depend on the component

    async def reset(self) -> None:
        """Attempt to reset the machinery item.

        If the state indicates resetting is required, it is safe to call when the machinery item is most states.

        If a "Startup" skill for this machinery item exists, reset is expected to be performed by the startup skill.
        If there is no "Startup" skill, then reset all skills and subcomponents.

        :raises PyUaRuntimeError: If the machinery item is in state `MachineryItemState.NOT_AVAILABLE`
        """
        match self._current_state:
            case MachineryItemState.EXECUTING | MachineryItemState.NOT_EXECUTING:
                self.logger.debug(
                    "Skipping reset() of machinery item",
                    state=self._current_state.name,
                )
                return
            case MachineryItemState.OUT_OF_SERVICE:
                try:
                    startup_skill = self.startup_skill
                    self.logger.debug("Resetting with startup skill")
                    await self.reset_skills([startup_skill])  # make sure the startup skill is ready
                    if startup_skill.current_state == SkillState.READY:
                        await startup_skill.start()
                        await startup_skill.wait_for_state(SkillState.RUNNING)
                    else:
                        self.logger.debug(
                            "Startup skill is already in target state",
                            state=startup_skill.current_state.name,
                        )
                except KeyError:  # no startup skill found
                    self.logger.debug("Resetting without startup skill")
                    await self.reset_components()
                    await self.reset_skills()
            case _:
                msg = f"Cannot reset machinery item '{self.path}' from state='{self._current_state.name}'!"
                raise OpenSmiRuntimeError(msg)

    async def reset_components(self, components: Iterable[BaseComponent] | None = None) -> None:
        """Reset given components. If None given, resets all own components."""
        if components is None:
            components = self._components

        for component in components:
            self.logger.debug("Resetting component...", component=str(component.path))
            await component.reset()

    async def reset_skills(self, skills: dict[str, AbstractSkill] | Iterable[AbstractSkill] | None = None) -> None:
        """Reset all given skills. If None given, resets all own skills."""
        if skills is None:
            skills = self.skill_set
        elif isinstance(skills, dict):
            skills = skills.values()

        try:
            await reset_skills_parallel_and_wait(skills)  # pyright: ignore[reportArgumentType]
        except Exception as ex:
            await self.ua_log_error(f"Error resetting skills: {ex}")
            await self.set_current_state(MachineryItemState.OUT_OF_SERVICE)
            # raise ua.UaStatusCodeError(ua.StatusCodes.BadInvalidState) from ex
            raise

    async def halt_components(self, components_to_halt: Iterable[str] | Iterable[BaseComponent] | None = None) -> None:
        """Halt all components (None, default) of this machinery item or only specific ones.

        :param components_to_halt: All components or only specific ones. Note: components by name will only be searched
            for in this machinery item!
        """
        for component in self._components:  # halt all requested components
            if components_to_halt is not None and (
                component.name not in components_to_halt  # pyright: ignore[reportOperatorIssue]
                or component not in components_to_halt  # pyright: ignore[reportOperatorIssue]
            ):
                self.logger.debug("Skipping halting component, because it should not be halted.", component=component)
                continue
            if component.name.lower().startswith("port_"):  # TODO(CaHa): Let each component decide, not like this
                self.logger.debug("Skipping halting component, because it is a port.", component=component)
                continue

            try:
                self.logger.debug("Trying to halt component...", component=component)
                await component.halt(components_to_halt=components_to_halt)
            except Exception:
                await self.ua_log_error(f"Could not halt component '{component.full_name}'!")
                self.logger.exception("Could not halt component!", component=component)

    async def halt_skills(self, skills_to_halt: Iterable[str] | Iterable[AbstractSkill] | None = None) -> None:
        """Halt all skills (``None``, default) of this machinery item or only specific ones.

        Note: skills by name will only be searched for in this machinery item!
        """
        if skills_to_halt is None:  # halt all own skills
            await halt_skills_parallel_and_wait(self.skill_set)
        else:  # halt only specific skills
            new_skills_to_halt: list[AbstractSkill] = []
            for skill in skills_to_halt:  # convert str into BaseSkill instances
                if isinstance(skill, str):
                    try:
                        new_skills_to_halt.append(self.skill_set[skill])
                    except KeyError:
                        self.logger.warning("Could not find skill in own skill set!", skill_name=skill)
                elif isinstance(skill, AbstractSkill):
                    new_skills_to_halt.append(skill)

            await halt_skills_parallel_and_wait(new_skills_to_halt)

    async def halt(
        self,
        *,
        components_to_halt: Iterable[str] | Iterable[BaseComponent] | None = None,
        skills_to_halt: Iterable[str] | Iterable[AbstractSkill] | None = None,
    ) -> None:
        """Halt the machinery item.

        If a "Startup" skill for this machinery item exists, halt is expected to be performed by the startup skill.
        Otherwise, halt all/no skills and all/no subcomponents.

        Note: The startup skill should not simply call this method, it will not halt anything!

        :param components_to_halt: Whether to halt all subcomponents (except Ports) (None, default) or specified ones.
        :param skills_to_halt: Whether to halt all skills (None, default) or specified ones.
        """
        await self.set_current_state(MachineryItemState.OUT_OF_SERVICE)
        await self.set_current_operation_mode(MachineryOperationMode.MAINTENANCE)

        try:
            startup_skill = self.startup_skill
            self.logger.debug("Halting with startup skill")
            await halt_skills_parallel_and_wait([startup_skill])
        except KeyError:  # no startup skill
            self.logger.debug(
                "Halting without startup skill", components_to_halt=components_to_halt, skills_to_halt=skills_to_halt
            )
            await asyncio.gather(self.halt_components(components_to_halt), self.halt_skills(skills_to_halt))

    @lifecycle(
        after=(
            AttributesMixin.lifecycle_attributes,
            MonitoringMixin.lifecycle_monitoring,
            ParameterSetMixin.lifecycle_parameter_set,
        )
    )
    async def lifecycle_machinery_item(self) -> AsyncGenerator[None]:
        """Lifecycle method which calls `_init` for initialization and `_shutdown` for shutdown."""
        await self._init()
        yield
        await self._shutdown()

    @abstractmethod
    async def _init(self) -> None:
        pass

    async def _shutdown(self) -> None:
        for component in self._components:
            try:
                await component.shutdown()
            except (Exception, asyncio.CancelledError):  # noqa: PERF203
                # We don't know what might go wrong, catch all just in case
                self.logger.exception("Sub-component shutdown error!", sub_component=component.name)

    @property
    def startup_skill(self) -> AbstractSkill:
        """Return the startup skill of the machinery item.

        :raises KeyError: If this machinery item has no startup skill.
        """
        from open_smi_server.base_startup_skill import BaseStartupSkill

        return self.skill_set[BaseStartupSkill.NAME]

    @override
    async def condition_as_dependency_ready(self, _user: UserAuthorization) -> bool:
        if self.current_state not in (MachineryItemState.EXECUTING, MachineryItemState.NOT_EXECUTING):
            msg = f"Denied, required component '{self.full_name}' is not ready!"
            await self.ua_log_error(msg)
            raise ua.UaStatusCodeError(UaStatusCodes.BadStateNotActive)

        return True


class BaseComponent(IdentificationMixin[MachineryComponentIdentification], BaseMachineryItem):
    """Base class for all components (= OPC UA machinery items)."""

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=SmartFactoryMachineSetNodeIds.ComponentType)  # generic component type
