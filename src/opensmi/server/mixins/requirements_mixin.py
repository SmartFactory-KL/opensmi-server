# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixin providing requirement/dependency management capabilities for `UaObject`."""

from __future__ import annotations

from collections.abc import Sequence

from asyncua import ua
from asyncua.common.node import Node
from asyncua.ua.uaerrors import BadNoMatch
from opensmi.core.nodesets import DiNodeIds, SmartFactorySkillSetNodeIds
from opensmi.core.ua_node_util import get_child_without_ns, get_node_id
from typing_extensions import override

from opensmi.server.interfaces import AbstractUaObject
from opensmi.server.ua_object import UaObject


class RequirementsMixin(AbstractUaObject):
    """Mixin providing requirement/dependency management capabilities for `UaObject`."""

    # Lazily initialized instead of in a constructor, so this mixin does not need a cooperative ``__init__``.
    _requirements_dependencies: list[UaObject] | None = None
    """ Dependencies that are checked before a skill/method can be executed. """
    _ua_requirements_folder: Node | None = None
    """ OPC UA folder node organizing all requirements. Is initialized as soon as the first requirement is added."""

    async def add_dependency(self, other: UaObject) -> None:
        """Add the given `UaObject` (skill, port, resource, etc.) as a dependency.

        Influences the conditions before this skill/method can be executed.
        """
        assert isinstance(other, UaObject), f"Given dependency '{other.__class__.__name__}' is invalid!"

        if self._ua_requirements_folder is None:
            self._ua_requirements_folder = await self._get_or_create_requirements_folder()

        target = other.ua_main_node
        await self._ua_requirements_folder.add_reference(
            target=target.nodeid,
            reftype=ua.FourByteNodeId(ua.Int32(ua.object_ids.ObjectIds.Requires)),
            forward=True,
            bidirectional=True,
        )
        if self._requirements_dependencies is None:
            self._requirements_dependencies = []
        self._requirements_dependencies.append(other)

        self.logger.debug("Added dependency reference", target=target.nodeid.to_string())

    async def _get_or_create_requirements_folder(self) -> Node:
        browse_name = ua.QualifiedName(
            Name="Requirements",
            NamespaceIndex=self.server.ua_get_namespace_index(SmartFactorySkillSetNodeIds),
        )
        parent = self.ua_main_node

        try:
            return await get_child_without_ns(parent=parent, display_name=browse_name.Name)
        except BadNoMatch:
            self.logger.debug("Adding missing requirements node")
            return await parent.add_object(
                nodeid=get_node_id(parent, name=browse_name),
                bname=browse_name,
                objecttype=self.server.ua_get_node_id(DiNodeIds.FunctionalGroupType),
                instantiate_optional=False,
            )

    @property
    @override
    def dependencies(self) -> Sequence[UaObject]:
        """Dependencies of this `UaObject`, added via `add_dependency`."""
        return self._requirements_dependencies or ()
