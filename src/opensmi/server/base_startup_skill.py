# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Final, Never

from opensmi.core import MachineryItemState, MachineryOperationMode, SkillState
from typing_extensions import override

from opensmi.server import BaseSkillContinuous
from opensmi.server.mixins import ParentMixin
from opensmi.server.nodesets import SmartFactoryMachineSetNodeIds
from opensmi.server.ua_object import UaObjectDefinition

if TYPE_CHECKING:
    from opensmi.server.base_machinery_item import BaseMachineryItem as BaseMachineryItem
    from opensmi.server.protocols import UserAuthorization


class BaseStartupSkill(ParentMixin["BaseMachineryItem"], BaseSkillContinuous):
    """The base for every startup skill. Manges status and mode changes of the parent `BaseMachineryItem`.

    - Startup skill not running --> `MachineryItemState.OUT_OF_SERVICE` state
    - 1+ finite skills running --> `MachineryItemState.EXECUTING` state / `MachineryOperationMode.PROCESSING` mode
    - 0 finite skills running --> `MachineryItemState.NOT_EXECUTING` state / ``MachineryOperationMode.SETUP`` mode

        (see SF-KL Skill specification V4).

    Startup skills are never suspendable and have neither separate precondition nor feasibility checks.

    The default implementation resets all subcomponents and skills of the parent component when in state
    `SkillState.STARTING` and halts all when in state `SkillState.HALTING`.
    Override the corresponding ``_handle_*`` methods for custom behavior.
    """

    NAME: Final[str] = "StartupSkill"
    _handle_status_change_task: asyncio.Task | None = None

    def __init__(
        self,
        *,
        minimum_access_level: int = 2,
        handle_status_change: bool = True,
        **kwargs: dict[str, Any],
    ) -> None:
        """*Cooperative* constructor."""
        super().__init__(
            name=BaseStartupSkill.NAME,
            suspendable=False,
            minimum_access_level=minimum_access_level,
            precondition_check=None,
            feasibility_check=None,
            **kwargs,
        )

        if handle_status_change:
            self._handle_status_change_task = asyncio.create_task(
                self._handle_status_changes(), name=f"{self.name}_handle_status_changes"
            )

    @override
    async def _condition_startup_completed(self, user: UserAuthorization) -> bool:
        """Return ``True``.

        A Startup skill in state `SkillState.RUNNING` is required for parent's "Startup" being completed.
        A normal skill would check through the parent, but we need to always be able to start.
        """
        return True

    async def _handle_status_changes(self) -> Never:
        while True:
            await asyncio.sleep(0.1)  # TODO(CaHa): Event based?

            if self.current_state != SkillState.RUNNING:
                await self.parent.set_current_state(MachineryItemState.OUT_OF_SERVICE)
                await self.parent.set_current_operation_mode(MachineryOperationMode.NONE)
                continue
            # else:
            # we only consider finite skills
            finite_skill_states: list[SkillState] = [
                skill.current_state for skill in self.parent.skill_set if skill.is_finite
            ]

            # there is at least 1 finite skill running/suspended
            if finite_skill_states.count(SkillState.RUNNING) > 0 or finite_skill_states.count(SkillState.SUSPENDED) > 0:
                await self.parent.set_current_state(MachineryItemState.EXECUTING)
                await self.parent.set_current_operation_mode(MachineryOperationMode.PROCESSING)
            else:
                await self.parent.set_current_state(MachineryItemState.NOT_EXECUTING)
                await self.parent.set_current_operation_mode(MachineryOperationMode.SETUP)

    @override
    async def _handle_starting(self) -> None:
        await self.parent.reset_components()
        await self.parent.reset_skills()

    @override
    async def _handle_halting(self) -> None:
        # this skill is important for the machine/component to function correctly,
        # so halt the machine/component if this skill is halted
        await self.parent.halt_components()
        await self.parent.halt_skills()

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        definition = await super()._get_definition()
        definition.namespace_uri = SmartFactoryMachineSetNodeIds  # the StartupSkill must be in this namespace
        return definition
