# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from typing import TYPE_CHECKING, Protocol

from asyncua import ua

if TYPE_CHECKING:
    import structlog
    from asyncua.common.node import Node
    from asyncua.crypto.permission_rules import UserRole
    from asyncua.server.internal_session import SessionState

    from open_smi_server.base_machine import BaseMachine
    from open_smi_server.lock import Lock
    from open_smi_server.ua_object import UaObject
    from open_smi_server.ua_variable_containers import Monitoring


class UserIdentity(Protocol):
    """Provides immutable user identity information."""

    @property
    def name(self) -> str:
        """Unique username."""
        ...

    @property
    def full_name(self) -> str:
        """Absolute username."""
        ...


class UserAuthentication(UserIdentity, Protocol):
    """Provides user authentication."""

    def check_password(self, password: bytes | str | None) -> bool:
        """Return whether ``password`` authenticates the user."""
        ...


class UserAuthorization(UserIdentity, Protocol):
    """Represents a user's authorization state."""

    @property
    def current_access_level(self) -> int:
        """Current access level of the user.

        Read/write. The value must be between 0 and ``maximum_access_level``.
        """
        ...

    @current_access_level.setter
    def current_access_level(self, access_level: int) -> None: ...

    @property
    def maximum_access_level(self) -> int:
        """Maximum access level the user may be assigned."""
        ...

    @property
    def priority(self) -> int:
        """Authorization priority.

        Read-only. Higher values take precedence when resolving authorization conflicts.
        """
        ...

    @property
    def role(self) -> "UserRole":
        """The user's ``asyncua`` role.

        Read-only. Always ``UserRole.User`` for normal users. Internal user uses ``UserRole.Admin``.
        """
        ...


class UserPresence(UserIdentity, Protocol):
    """Tracks whether the user currently has active sessions."""

    @property
    def present(self) -> bool:
        """Whether the user currently has at least one active session."""
        ...

    @property
    def sessions(self) -> set["SessionProtocol"]:
        """Active sessions (Read-only)."""
        ...

    async def add_session(self, session: "SessionProtocol") -> None:
        """Register an active session for the user."""
        ...

    async def remove_session(self, session: "SessionProtocol") -> None:
        """Unregister a previously added active session."""
        ...


class UserProtocol(UserAuthentication, UserAuthorization, UserPresence, Protocol):
    """Complete public ``User`` interface."""


class SessionProtocol(Protocol):
    name: str
    user: "UserProtocol"  # This is different from normal asyncua, but correct in our case
    state: "SessionState"

    async def close_session(self, delete_subs: bool = True) -> None: ...

    def is_activated(self) -> bool: ...


class Lockable(Protocol):
    @property
    def lock(self) -> "Lock": ...

    def access_allowed(self, user: UserProtocol) -> bool: ...


class _HasLogger(Protocol):
    @property
    def logger(self) -> "structlog.stdlib.BoundLogger": ...


class _HasUaNode(Protocol):
    @property
    def ua_node(self) -> "Node": ...


class _HasMonitoring(Protocol):
    @property
    def monitoring(self) -> "Monitoring": ...


class _HasParent(Protocol):
    @property
    def parent(self) -> "UaObject": ...


class _HasRootParent(Protocol):
    @property
    def root_parent(self) -> "BaseMachine": ...


class _HasName(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def full_name(self) -> str: ...


class UaObjectProtocol(Lockable, _HasLogger, _HasUaNode, _HasRootParent, _HasParent, _HasName, Protocol):
    """Full `UaObject` interface."""


class HasLocalizedText(Protocol):
    """Protocol for objects that provide an OPC UA localized text representation."""

    @property
    def localized_text(self) -> "ua.LocalizedText":
        """Return the OPC UA localized text representation."""
        ...
