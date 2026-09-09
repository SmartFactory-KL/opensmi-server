# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""All abstract interfaces used in the server."""

from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from asyncua import ua
from asyncua.common.methods import uamethod
from asyncua.common.node import Node
from asyncua.ua import NodeId
from asyncua.ua.status_codes import StatusCodes as UaStatusCodes
from structlog.stdlib import BoundLogger
from typing_extensions import deprecated

from open_smi_common.enums import SkillState
from open_smi_server.protocols import SessionProtocol, UserAuthorization

if TYPE_CHECKING:
    from open_smi_server._server import Server
    from open_smi_server.lock import Lock
    from open_smi_server.ua_object import UaObjectDefinition


class AbstractUaObject(ABC):
    """Abstract interface for all `UaObject` implementations."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the UaObject."""

    @property
    @deprecated("Use path instead")
    @abstractmethod
    def full_name(self) -> str:
        """Return the full name including the names of all parents, separated by '/'."""

    @property
    @abstractmethod
    def path(self) -> PurePosixPath:
        """Return the full path of the object."""

    @property
    @abstractmethod
    def ua_node(self) -> Node:
        """Raw OPC UA node.

        Do not touch unless you know what you are doing!
        """

    @property
    @abstractmethod
    def server(self) -> Server:
        """Server instance."""

    @property
    @abstractmethod
    def logger(self) -> BoundLogger:
        """Logger associated with the `UaObject` instance."""

    @property
    @abstractmethod
    def lock(self) -> Lock:
        """`Lock` instance protecting this `UaObject`."""

    @abstractmethod
    def access_allowed(self, user: UserAuthorization, *, lock_required: bool | None = None) -> bool:
        """Check whether ``user`` is currently allowed access to this `UaObject`.

        :param user: `User` to check.
        :param lock_required: Whether the corresponding `Lock` must also be owned by ``user``. Default ``None`` uses
            ``bypass_lock`` setting.
        """

    async def condition_as_dependency_ready(self, _user: UserAuthorization) -> bool:
        """Check whether this object is ready as a dependency in composite skills, etc.

        Subclasses that can be used as a dependency **must** override this method. Raises by default.

        :return: Always ``True``, else an exception is raised.
        :raises UaStatusCodeError: If any dependency is not ready.
        """
        raise ua.UaStatusCodeError(UaStatusCodes.BadStateNotActive)


class AbstractCallable(AbstractUaObject):
    """Callable interface, shared base for `AbstractSkill` and `AbstractMethod`."""

    @abstractmethod
    async def _condition_dependencies_ready(self, _user: UserAuthorization) -> bool:
        """Check whether **ALL** dependencies are ready (different meaning for different types).

        :return: Always ``True``, else an exception is raised.
        :raises UaStatusCodeError: If any dependency is not ready.
        """

    @abstractmethod
    async def _condition_startup_completed(self, user: UserAuthorization) -> bool:
        """Check whether the parent component/machine startup is completed."""

    @abstractmethod
    async def _condition_access_allowed(self, user: UserAuthorization) -> bool:
        """Check if the provided ``user`` has access to this skill.

        The `User` must either be an admin (asyncua internal user), or must own the `Lock` protecting this
        skill and a current access level greater or equal to this skill's minimum access level.
        """


class AbstractSkill(AbstractCallable):
    """Skill interface for internal and external OPC UA access."""

    @abstractmethod
    async def enable_historizing(self, *, count: int = 1000) -> None:
        pass

    @property
    @abstractmethod
    def is_finite(self) -> bool:
        """Return ``True`` if this skill is finite.

        This must be correct regardless where the logic is implemented (PLC, Remote, etc. vs. this Python instance).
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def current_state(self) -> SkillState:
        """Current state of the skill."""
        raise NotImplementedError

    @abstractmethod
    async def wait_for_state(self, state: SkillState, *, timeout: float | None = 60) -> None:
        """Blocks until this skill either reached the given ``state`` or was halted or the ``timeout`` was reached.

        :param state: the state of this skill to wait for.
        :param timeout: timeout in seconds, maximum wait time.
        :raises asyncio.TimeoutError: if the ``timeout`` is reached.
        """

    @property
    @abstractmethod
    def name(self) -> str:
        """Name of the skill."""
        raise NotImplementedError

    @property
    @abstractmethod
    def ua_state_machine(self) -> Node:
        """OPC UA base node of state machine."""

    @abstractmethod
    async def _get_definition(self) -> UaObjectDefinition:  # for UaObject
        """Return the instantiation definition for the main OPC UA node. Is called from `ua_create_node`."""

    ######################################
    # Internal skill interface (for composite skills)

    @abstractmethod
    async def start(self) -> None:
        """Start the skill. Machine internal interface.

        Similar to calling the OPC UA method ``start()``, except that exceptions are not handled.
        """
        raise NotImplementedError

    @abstractmethod
    async def suspend(self) -> None:
        """Suspend the skill (if the skill is suspendable). Machine internal interface.

        Similar to calling the OPC UA method ``suspend()``, except that exceptions are not handled.
        """
        raise NotImplementedError

    @abstractmethod
    async def reset(self) -> None:
        """Reset the skill. Machine internal interface.

        Similar to calling the OPC UA method ``reset()``, except that exceptions are not handled.
        """
        raise NotImplementedError

    @abstractmethod
    async def halt(self) -> None:
        """Halt (Stop) the skill. Machine internal interface.

        Similar to calling the OPC UA method ``halt()``, except that exceptions are not handled.
        """
        raise NotImplementedError

    ######################################
    # OPC-UA Callbacks

    @uamethod
    @abstractmethod
    async def _ua_start(self, _parent: NodeId, session: SessionProtocol) -> ua.StatusCode:
        """Handle the OPC UA ``start`` method invocation.

        This callback is executed when an OPC UA client calls the ``start`` method. Do **not** raise
        exceptions here, instead return the appropriate OPC UA status code!

        :param _parent: Parent OPC UA ``NodeId``.
        :param session: OPC UA Client session that issued the method request.
        """
        return ua.StatusCode(ua.UInt32(UaStatusCodes.BadNotImplemented))

    @uamethod
    @abstractmethod
    async def _ua_suspend(self, _parent: NodeId, session: SessionProtocol) -> ua.StatusCode:
        """Handle the OPC UA ``suspend`` method invocation.

        This callback is executed when an OPC UA client calls the ``suspend`` method. Do **not** raise
        exceptions here, instead return the appropriate OPC UA status code!

        :param _parent: Parent OPC UA ``NodeId``.
        :param session: OPC UA Client session that issued the method request.
        """
        return ua.StatusCode(ua.UInt32(UaStatusCodes.BadNotImplemented))

    @uamethod
    @abstractmethod
    async def _ua_reset(self, _parent: NodeId, session: SessionProtocol) -> ua.StatusCode:
        """Handle the OPC UA ``reset`` method invocation.

        This callback is executed when an OPC UA client calls the ``reset`` method. Do **not** raise
        exceptions here, instead return the appropriate OPC UA status code!

        :param _parent: Parent OPC UA ``NodeId``.
        :param session: OPC UA Client session that issued the method request.
        """
        return ua.StatusCode(ua.UInt32(UaStatusCodes.BadNotImplemented))

    @uamethod
    @abstractmethod
    async def _ua_halt(self, _parent: NodeId, session: SessionProtocol) -> ua.StatusCode:
        """Handle the OPC UA ``halt`` method invocation.

        This callback is executed when an OPC UA client calls the ``halt`` method. Do **not** raise
        exceptions here, instead return the appropriate OPC UA status code!

        :param _parent: Parent OPC UA ``NodeId``.
        :param session: OPC UA Client session that issued the method request.
        """
        return ua.StatusCode(ua.UInt32(UaStatusCodes.BadNotImplemented))

    @abstractmethod
    async def _condition_is_suspendable(self, _user: UserAuthorization) -> bool:
        """Check whether this skill is suspendable.

        :return: Always ``True``, else an exception is raised.
        :raises UaStatusCodeError: If the skill is not suspendable.
        """


class AbstractMethod(AbstractCallable):
    """Interface for methods."""

    @abstractmethod
    async def execute_method(self) -> None:
        """Implement subclass-specific method logic."""
        raise NotImplementedError


class AbstractUaLogger(ABC):
    """Abstract interface for OPC UA logging."""

    @abstractmethod
    async def ua_log_info(self, msg: str, *, severity: int = 0, source: AbstractUaObject | None = None) -> None:
        """Log an informational message."""

    @abstractmethod
    async def ua_log_warning(self, msg: str, *, severity: int = 555, source: AbstractUaObject | None = None) -> None:
        """Log a warning message."""

    @abstractmethod
    async def ua_log_error(
        self, msg: str, *, severity: int = 999, code: str = "", source: AbstractUaObject | None = None
    ) -> None:
        """Log an error message, with an optional, application-specific error ``code``."""
