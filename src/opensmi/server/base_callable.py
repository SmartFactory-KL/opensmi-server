# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Abstract base class for all callables, i.e. skills (`BaseSkill`) and methods (`BaseMethod`)."""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import AsyncGenerator

from asyncua import ua
from asyncua.common.node import Node
from asyncua.crypto.permission_rules import UserRole
from asyncua.ua.uaerrors import BadNoMatch
from opensmi.core.lifecycle_mixin import lifecycle
from opensmi.core.ua_node_util import get_node_id
from typing_extensions import override

from opensmi.server.interfaces import AbstractCallable
from opensmi.server.mixins import NotificationForwarderMixin
from opensmi.server.mixins.ua_variable_container_mixins import (
    FinalResultDataMixin,
    MonitoringMixin,
    ParameterSetMixin,
)
from opensmi.server.nodesets import SmartFactorySkillSetNodeIds
from opensmi.server.protocols import UserAuthorization
from opensmi.server.ua_object import UaObject


class BaseCallable(NotificationForwarderMixin, UaObject, AbstractCallable):
    """Abstract base class for all callables, i.e. skills (`BaseSkill`) and methods (`BaseMethod`)."""

    _ua_sub_node: Node

    def __init__(
        self,
        *,
        name: str | None = None,
        minimum_access_level: int | None = None,
        parent: UaObject | None = None,
        **kwargs,
    ) -> None:
        """Create a new callable instance.

        :param name: name of the callable.
        :param minimum_access_level: Minimum required access level to start the callable (see User for more info)
        """
        super().__init__(name=name, minimum_access_level=minimum_access_level, parent=parent, **kwargs)

    @abstractmethod
    async def _get_sub_node(self) -> Node:
        raise NotImplementedError

    @lifecycle(
        before=(
            ParameterSetMixin.lifecycle_parameter_set,
            MonitoringMixin.lifecycle_monitoring,
            FinalResultDataMixin.lifecycle_final_result_data,
        )
    )
    async def lifecycle_ua_sub_node(self) -> AsyncGenerator[None]:
        """Initialize name, sub node, etc."""
        ns = self.server.ua_get_namespace_index(SmartFactorySkillSetNodeIds)
        ua_name = await self.ua_node.get_child(f"{ns}:Name")
        await ua_name.write_value(self.name)

        self._ua_sub_node = await self._get_sub_node()
        assert self._ua_sub_node is not None

        ua_min_access_level_browse_name = f"{ns}:MinAccessLevel"
        try:
            ua_min_access_level_node = await self._ua_sub_node.get_child(ua_min_access_level_browse_name)
            await ua_min_access_level_node.write_value(ua.Int32(self.minimum_access_level))
        except BadNoMatch:  # optional in nodeset
            node_id = get_node_id(self._ua_sub_node, name=ua_min_access_level_browse_name, ns_idx=None)
            await self._ua_sub_node.add_variable(
                node_id,
                ua_min_access_level_browse_name,
                ua.Int32(self.minimum_access_level),
            )

        yield
        # no shutdown

    @lifecycle(
        after=(
            ParameterSetMixin.lifecycle_parameter_set,
            MonitoringMixin.lifecycle_monitoring,
            FinalResultDataMixin.lifecycle_final_result_data,
        )
    )
    async def lifecycle_callable(self) -> AsyncGenerator[None]:
        """Lifecycle method which calls `_init` for initialization and `_shutdown` for shutdown."""
        await self._init()
        yield
        await self._shutdown()

    async def _init(self) -> None:
        pass

    async def _shutdown(self) -> None:
        pass

    @property
    @override
    def ua_node(self) -> Node:
        if hasattr(self, "_ua_sub_node"):
            return self._ua_sub_node  # deal with our hierarchy
        assert self._ua_node is not None
        return self._ua_node

    @ua_node.setter
    def ua_node(self, ua_node: Node) -> None:
        UaObject.ua_node.fset(self, ua_node)  # pyright: ignore[reportOptionalCall]

    @override
    async def _condition_access_allowed(self, user: UserAuthorization) -> bool:
        if user.role == UserRole.Admin:
            return True
        if user.current_access_level < self.minimum_access_level:
            await self.ua_log_error(
                f"Denied, user '{user.name}' has no access to '{self.path}' "
                f"(minimum access level of {self.minimum_access_level} required)!"
            )
            raise ua.uaerrors.BadUserAccessDenied
        if self.lock.locking_user != user:
            await self.ua_log_error(
                f"Denied, user '{user.name}' does not own the lock '{self.lock.path}' required to access '{self.path}'!",
                code="E123",
            )
            raise ua.uaerrors.BadRequiresLock
        return True

    @override
    async def _condition_startup_completed(self, user: UserAuthorization) -> bool:
        from opensmi.server.base_machinery_item import BaseMachineryItem  # prevent circular import

        parent = getattr(self, "parent", None)
        assert isinstance(parent, BaseMachineryItem), f"{parent} is no valid parent!"
        return await parent.condition_startup_completed(user)

    @override
    async def _condition_dependencies_ready(self, _user: UserAuthorization) -> bool:
        for dependency in getattr(self, "dependencies", ()):
            assert isinstance(dependency, UaObject), f"'{dependency}' is not of type UaObject"
            await dependency.condition_as_dependency_ready(_user)

        return True
