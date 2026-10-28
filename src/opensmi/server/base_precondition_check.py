# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Base class for precondition checks for skills."""

from asyncua.common.node import Node
from typing_extensions import override

from opensmi.server.base_skill import BaseCheck
from opensmi.server.mixins.skill_logic_mixins import FiniteSkillLogicMixin
from opensmi.server.nodesets import SmartFactoryMachineSetNodeIds
from opensmi.server.ua_object import UaObjectDefinition


class BasePreconditionCheck(FiniteSkillLogicMixin, BaseCheck):
    """Base class for precondition checks for skills."""

    def __init__(self) -> None:
        super().__init__(name="PreconditionCheck")

    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=SmartFactoryMachineSetNodeIds.SkillElementType)

    @override
    async def _get_sub_node(self) -> Node:
        return self.ua_node
