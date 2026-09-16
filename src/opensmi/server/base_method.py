# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Abstract base class for all methods."""

from __future__ import annotations

from asyncua import ua
from asyncua.common.methods import uamethod
from asyncua.common.node import Node
from asyncua.ua import NodeId
from typing_extensions import override

from opensmi.server.base_callable import BaseCallable
from opensmi.server.interfaces import AbstractMethod
from opensmi.server.nodesets import SmartFactorySkillSetNodeIds
from opensmi.server.protocols import SessionProtocol
from opensmi.server.ua_object import UaObjectDefinition


class BaseMethod(BaseCallable, AbstractMethod):
    """Abstract base class for all methods."""

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=SmartFactorySkillSetNodeIds.BaseMethodType)

    @override
    async def _get_sub_node(self) -> Node:
        return self.ua_node  # methods are not organized in sub nodes like skills are

    @override
    async def _init(self) -> None:
        await super()._init()

        ns = self.server.ua_get_namespace_index(SmartFactorySkillSetNodeIds)
        node_call = await self.ua_node.get_child(f"{ns}:Call")
        _session = self.server.ua_server.iserver.isession
        _session.add_method_callback(node_call.nodeid, self._call)

    @uamethod
    async def _call(self, _parent: NodeId, session: SessionProtocol) -> ua.StatusCode:
        try:
            await self._condition_access_allowed(session.user)
            await self._condition_startup_completed(session.user)
            await self._condition_dependencies_ready(session.user)
            await self.execute_method()
            return ua.StatusCode(ua.UInt32(ua.status_codes.StatusCodes.Good))
        except ua.UaStatusCodeError as e:
            return ua.StatusCode(e.code)
        except Exception:
            self.logger.exception("Exception while executing method!")
            return ua.StatusCode(ua.UInt32(ua.status_codes.StatusCodes.BadInternalError))
