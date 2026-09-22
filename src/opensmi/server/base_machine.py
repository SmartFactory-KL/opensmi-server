# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Abstract base class for a Smart Machine."""

from __future__ import annotations

from typing import TYPE_CHECKING

from opensmi.core.errors import ValidationError
from typing_extensions import override

from opensmi.server.base_machinery_item import BaseMachineryItem
from opensmi.server.mixins import LockableMixin, NotificationMixin
from opensmi.server.mixins.ua_variable_container_mixins import IdentificationMixin
from opensmi.server.nodesets import SmartFactoryMachineSetNodeIds
from opensmi.server.ua_object import UaObjectDefinition
from opensmi.server.ua_object_containers import Users
from opensmi.server.ua_variable_containers import MachineIdentification

if TYPE_CHECKING:
    from opensmi.server import Server


class BaseMachine(
    LockableMixin,
    NotificationMixin,
    IdentificationMixin[MachineIdentification],
    BaseMachineryItem,
):
    """Abstract base class for a Smart Machine."""

    def __init__(
        self,
        *,
        server: Server | None = None,
        name: str | None = None,
        minimum_access_level: int | None = None,
        **kwargs,
    ) -> None:
        """Create a new Machine instance. Server instance must be provided."""
        super().__init__(name=name, minimum_access_level=minimum_access_level, server=server, **kwargs)

        self._users = Users(parent=self)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=SmartFactoryMachineSetNodeIds.MachineType)

    @override
    async def _init(self) -> None:
        await super()._init()

        await self._init_status()
        await self._init_users()  # needs the MachineryBuildingBlocks node

    async def _init_users(self) -> None:
        """Initialize OPC UA representation of users."""
        assert self._ua_machinery_building_blocks is not None

        await self._users.ua_create_node(self._ua_machinery_building_blocks)
        await self._users.init()

        await self.server.access_control.init_ua_users(self._users)

    def _validate(self) -> None:
        """Validate whether the module was correctly set-up by the user.

        :raises ValidationError: If a validation error occurs.
        """
        from opensmi.server.base_startup_skill import BaseStartupSkill

        try:
            startup_skill = self.skill_set[BaseStartupSkill.NAME]
            if startup_skill.is_finite:
                msg = f"Startup Skill of '{self.path}' must be continuous!"
                raise ValidationError(msg) from None
        except KeyError:
            msg = f"No startup skill provided for '{self.path}'!"
            raise ValidationError(msg) from None

    @property
    def root_parent(self) -> BaseMachine:
        """Return the root parent, which in the case of machines is ourselves.

        Required for `ParentMixin` functionality.
        """
        return self
