# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixin providing requirement/dependency management capabilities for `UaObject`."""

from __future__ import annotations

from asyncua import ua
from asyncua.common.node import Node
from asyncua.ua.uaerrors import BadNoMatch

from open_smi_common.nodesets import DiNodeIds, SmartFactorySkillSetNodeIds
from open_smi_common.ua_node_util import get_child_without_ns, get_node_id
from open_smi_server.interfaces import AbstractUaObject
from open_smi_server.ua_object import UaObject


class RequirementsMixin(AbstractUaObject):
    """Mixin providing requirement/dependency management capabilities for `UaObject`."""

    __dependencies: list[UaObject] | None = None
    """ List of dependencies that will be checked before a skill/method can be executed. """
    __ua_requirements_folder: Node | None = None
    """ OPC UA folder node organizing all requirements. Is initialized as soon as the first requirement is added."""

    async def add_dependency(self, other: UaObject) -> None:
        """Add the given `UaObject` (skill, port, resource, etc.) as a dependency.

        Influences the conditions before this skill/method can be executed:
        * If the dependency is a port, it must be coupled.
        * If the dependency is a finite skill, it must be ready.
        * If the dependency is a continuous skill, it must be running.
        """
        assert isinstance(other, UaObject), f"Given dependency '{other.__class__.__name__}' is invalid!"

        if self.__ua_requirements_folder is None:
            await self._init_requirements()
            assert self.__ua_requirements_folder is not None  # make the type checker happy

        target = other.ua_node.nodeid

        await self.__ua_requirements_folder.add_reference(
            target=target,
            reftype=ua.FourByteNodeId(ua.Int32(ua.object_ids.ObjectIds.Requires)),
            forward=True,
            bidirectional=True,
        )
        self.dependencies.append(other)

        self.logger.debug("Added dependency reference", target=target.to_string())

    async def _init_requirements(self) -> None:
        # initialize requirements folder on demand
        browse_name = ua.QualifiedName(
            Name="Requirements",
            NamespaceIndex=self.server.ua_get_namespace_index(SmartFactorySkillSetNodeIds),
        )
        try:
            self.__ua_requirements_folder = await get_child_without_ns(
                self.ua_node,
                display_name=browse_name.Name,
            )
        except BadNoMatch:
            self.logger.debug("Adding missing requirements node")
            self.__ua_requirements_folder = await self.ua_node.add_object(
                nodeid=get_node_id(self.ua_node, name=browse_name),
                bname=browse_name,
                objecttype=self.server.ua_get_node_id(DiNodeIds.FunctionalGroupType),
            )

    @property
    def dependencies(self) -> list[UaObject]:
        """List of dependencies of this `UaObject`."""
        if self.__dependencies is None:
            self.__dependencies = []
        return self.__dependencies
