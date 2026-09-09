# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from asyncua.ua import QualifiedName
from typing_extensions import override

from open_smi_common.nodesets import SmartFactorySkillSetNodeIds
from open_smi_server import BaseSkillFinite


class BaseFeasibilityCheck(BaseSkillFinite):
    """Base class for feasibility checks for skills."""

    @override
    async def _get_sub_node(self):
        ns_index = self.server.ua_get_namespace_index(SmartFactorySkillSetNodeIds)
        return await self.ua_node.get_child(QualifiedName("FeasibilityCheck", ns_index))
