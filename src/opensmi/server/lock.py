# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""``UaObject`` for managing exclusive access to its parent and its children."""

import asyncio
import contextlib
import time
from asyncio import Task
from collections.abc import AsyncGenerator
from typing import Any, Never

from asyncua import ua
from asyncua.common.methods import uamethod
from asyncua.common.node import Node
from asyncua.crypto.permission_rules import UserRole
from asyncua.ua.status_codes import StatusCodes
from opensmi.core.lifecycle_mixin import lifecycle
from typing_extensions import override

from opensmi.server.mixins.parent_mixin import ParentMixin
from opensmi.server.nodesets import DiNodeIds
from opensmi.server.protocols import SessionProtocol, UserAuthorization
from opensmi.server.ua_object import UaObject, UaObjectDefinition


class Lock(UaObject, ParentMixin[UaObject]):
    """Manages exclusive write/execute access rights to the parent `UaObject` and its children.

    Locks are not hierarchical. A child (or children's child, etc.) with its own Lock will not be
    influenced by this lock.

    OPC UA clients first need to request the lock by calling ``InitLock()``. If granted, they now own the lock and get
    exclusive write and execution rights. (reading, browsing, etc. is always possible). Ideally, clients release
    the lock as soon as they are done by calling ``ExitLock()``. Locks are automatically released when the client
    disconnects or is inactive for too long without calling ``RenewLock()``.

    The request can also fail when the user does not have the required minimum access level for the lock, or the lock
    is already hold by another client. If it's hold by another client, the client can request the lock by calling
    ``BreakLock()``. It will succeed only when the priority of the requesting client are larger than of the lock owner.

    Based on OPC UA Locking model (https://reference.opcfoundation.org/specs/OPC-10000-100/7).
    """

    # OPC UA variables we need to update regularly. They get initialized and checked in _init()
    _ua_locking_user: Node
    _ua_locked: Node
    _ua_locking_client: Node
    _ua_remaining_lock_time_ms: Node

    _task_check_inactivity: Task[None]

    def __init__(self, *, minimum_access_level: int | None = None, **kwargs: dict[str, Any]) -> None:
        """Construct new ``Lock`` instance with given ``minimum_access_level``."""
        super().__init__(
            name="Lock",
            minimum_access_level=minimum_access_level,
            bypass_lock=False,
            server=None,
            **kwargs,
        )

        self._locking_user: UserAuthorization | None = None
        self._locking_time: float = 0.0
        """ Monotonic timestamp when the lock was granted to a user. Used for inactivity timeout. """
        self._lock: asyncio.Lock = asyncio.Lock()
        """ Used to guarantee atomic updates. """

    async def init_lock(self, user: UserAuthorization, *, session: SessionProtocol | None = None) -> bool:
        """Request exclusive access to nodes protected by this ``Lock``.

        Request will fail if the lock is already owned or if ``user`` lacks necessary access rights.

        :return: Whether ``user`` now owns this ``Lock``.
        """
        if self.locked or not self.access_allowed(user, lock_required=False):
            return False

        await self.update_locking_status(user, session=session, reason="Init Lock")
        return True

    async def break_lock(self, user: UserAuthorization, *, session: SessionProtocol | None = None) -> bool:
        """Request exclusive access to nodes protected by this ``Lock``.

        Request will fail if the lock is already owned by a user with higher ``User.priority`` than ``user``
        or if ``user`` lacks necessary access rights. Warning: Will steal lock ownership from its current owner!

        :return: Whether ``user`` now owns this ``Lock``.
        """
        if not self.access_allowed(user, lock_required=False):
            return False
        if self.locking_user and user.role != UserRole.Admin and user.priority <= self.locking_user.priority:
            return False

        await self.update_locking_status(user, session=session, reason="Break Lock")
        return True

    async def exit_lock(self, user: UserAuthorization) -> bool:
        """Release exclusive access to nodes protected by this ``Lock``.

        Will fail if ``user`` lacks necessary access rights or the lock is owned by another user.

        :return: Whether the lock was released.
        """
        if not self.access_allowed(user, lock_required=True):
            return False

        await self.update_locking_status(None, reason="Exit Lock")
        return True

    async def renew_lock(self, user: UserAuthorization) -> bool:
        """Renew the remaining lock time before the ownership is lost due to inactivity."""
        if not self.access_allowed(user, lock_required=True):
            return False

        self._locking_time = time.monotonic()
        await self._ua_remaining_lock_time_ms.write_value(self.max_inactive_time_ms)
        return True

    async def _check_inactivity_loop(self) -> Never:
        while True:
            if self.locked:
                remaining_lock_time_ms = self.max_inactive_time_ms - int((time.monotonic() - self._locking_time) * 1000)
                if remaining_lock_time_ms <= 0:
                    await self.update_locking_status(None, reason="Inactivity")
                else:
                    await self._ua_remaining_lock_time_ms.write_value(remaining_lock_time_ms)
            await asyncio.sleep(1)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=DiNodeIds.LockingServicesType,
            namespace_uri=DiNodeIds,
            reference_type=ua.object_ids.ObjectIds.HasAddIn,
        )

    @lifecycle
    async def _lifecycle_lock(self) -> AsyncGenerator[None]:
        await self._init()
        yield
        await self._shutdown()

    async def _init(self) -> None:
        _ua_init_lock: Node | None = None
        _ua_exit_lock: Node | None = None
        _ua_break_lock: Node | None = None
        _ua_renew_lock: Node | None = None

        for node in await self.ua_node.get_children():
            match (await node.read_browse_name()).Name:  # ignore namespace
                case "LockingUser":
                    self._ua_locking_user = node
                case "Locked":
                    self._ua_locked = node
                case "LockingClient":
                    self._ua_locking_client = node
                case "RemainingLockTime":
                    self._ua_remaining_lock_time_ms = node
                case "InitLock":
                    _ua_init_lock = node
                case "ExitLock":
                    _ua_exit_lock = node
                case "BreakLock":
                    _ua_break_lock = node
                case "RenewLock":
                    _ua_renew_lock = node
                case _:
                    pass

        assert self._ua_remaining_lock_time_ms is not None
        assert self._ua_locked is not None
        assert self._ua_locking_client is not None
        assert self._ua_locking_user is not None
        assert _ua_init_lock is not None
        assert _ua_exit_lock is not None
        assert _ua_break_lock is not None
        assert _ua_renew_lock is not None

        await self.server.ua_server.historize_node_data_change(
            [self._ua_locking_user, self._ua_locking_client], count=1000
        )
        await self.update_locking_status(None, reason="Initialization")

        session = self.server.ua_server.iserver.isession
        _ = session.add_method_callback(_ua_init_lock.nodeid, self._ua_init_lock)  # pyright: ignore[reportAny]
        _ = session.add_method_callback(_ua_exit_lock.nodeid, self._ua_exit_lock)  # pyright: ignore[reportAny]
        _ = session.add_method_callback(_ua_break_lock.nodeid, self._ua_break_lock)  # pyright: ignore[reportAny]
        _ = session.add_method_callback(_ua_renew_lock.nodeid, self._ua_renew_lock)  # pyright: ignore[reportAny]

        # according to spec
        namespace_index = self.server.ua_get_namespace_index(DiNodeIds)
        ua_max_inactive_lock_time = self.server.ua_server.get_node(f"ns={namespace_index};i=6387")
        await ua_max_inactive_lock_time.write_value(self.max_inactive_time_ms)

        self.server.access_control.on_session_closed.connect(self._on_session_closed)

        self._task_check_inactivity = asyncio.create_task(
            self._check_inactivity_loop(), name=f"{self.full_name}.inactivity_check"
        )

        self.logger.debug("Initialized lock")

    async def _shutdown(self) -> None:
        with contextlib.suppress(asyncio.CancelledError):
            if (
                hasattr(self, "_task_check_inactivity")
                and self._task_check_inactivity
                and self._task_check_inactivity.cancel()
            ):
                await self._task_check_inactivity

    async def update_locking_status(
        self, user: UserAuthorization | None, *, session: SessionProtocol | None = None, reason: str = ""
    ) -> None:
        """Update locking status based on ``user`` unconditionally.

        :param user: ``User`` that will be set as the locking user. ``None`` for clearing the releasing the lock.
        :param session: (Optional) session instance. Is unavailable for internal user(s).
        :param reason: Short reason description. Only for logging purposes.
        """
        async with self._lock:
            if user is None:
                self.logger.info("Releasing lock", previous_user=str(self.locking_user), reason=reason)
                self._locking_user = None
                await self._ua_locking_client.write_value("")
                await self._ua_locking_user.write_value("")
                await self._ua_locked.write_value(False)
                await self._ua_remaining_lock_time_ms.write_value(0)
            else:
                self.logger.info("New lock owner", user=str(user), previous_user=str(self.locking_user), reason=reason)
                self._locking_user = user
                self._locking_time = time.monotonic()
                await self._ua_locking_user.write_value(user.name)
                if session is not None:  # session is not required for internal lock requests
                    # TODO(CaHa): locking client is supposed to be the client ApplicationUri
                    await self._ua_locking_client.write_value(str(session.name))
                await self._ua_locked.write_value(True)
                await self._ua_remaining_lock_time_ms.write_value(self.max_inactive_time_ms)

    @override
    def access_allowed(self, user: UserAuthorization, *, lock_required: bool | None = None) -> bool:
        """Check whether ``user`` is allowed to access this lock."""
        if lock_required is None:
            lock_required = not self.bypass_lock

        if user.role == UserRole.Admin:  # internal is always allowed
            return True

        if lock_required and self._locking_user != user:
            return False

        return user.current_access_level >= self.minimum_access_level

    async def _on_session_closed(self, session: SessionProtocol) -> None:
        """Handle session closed events.

        Used to check if the user of that session is owning a lock. If that is the case, it will be released.
        """
        if session.user == self.locking_user:
            await self.update_locking_status(None, reason="Session closed")

    # -----------------------------------------------------------------------------------------
    # OPC UA method callback handlers
    # -----------------------------------------------------------------------------------------

    @uamethod  # pyright: ignore[reportAny]
    async def _ua_init_lock(
        self, parent: ua.NodeId, session: SessionProtocol, context: str | None = None
    ) -> int | ua.StatusCode:
        self.logger.debug("UA InitLock() method called", user=str(session.user), context=context)
        assert session.user is not None

        if not self.access_allowed(session.user, lock_required=False):
            return ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied))

        if await self.init_lock(session.user, session=session):
            return 0  # InitLockStatus = OK

        # TODO(CaHa): spec wants InitLockStatus = -1 but asyncua says no
        return ua.StatusCode(ua.UInt32(StatusCodes.BadLocked))

    @uamethod  # pyright: ignore[reportAny]
    async def _ua_exit_lock(self, parent: ua.NodeId, session: SessionProtocol) -> int | ua.StatusCode:
        self.logger.debug("UA ExitLock() method called", user=str(session.user))
        assert session.user is not None

        if not self.access_allowed(session.user, lock_required=True):
            return ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied))

        if not self.locked:
            return -1  # ExitLockStatus = E_NotLocked

        if await self.exit_lock(session.user):
            return 0  # ExitLockStatus = OK

        return ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied))

    @uamethod  # pyright: ignore[reportAny]
    async def _ua_break_lock(self, parent: ua.NodeId, session: SessionProtocol) -> int | ua.StatusCode:
        self.logger.debug("UA BreakLock() method called", user=str(session.user))
        assert session.user is not None

        if not self.access_allowed(session.user, lock_required=False):
            return ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied))

        # BreakLockStatus:
        # 0  = OK
        # -1 = E_NotLocked
        ret = 0 if self.locked else -1

        if await self.break_lock(session.user, session=session):
            return ret

        return ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied))

    @uamethod  # pyright: ignore[reportAny]
    async def _ua_renew_lock(self, parent: ua.NodeId, session: SessionProtocol) -> int | ua.StatusCode:
        self.logger.debug("UA RenewLock() method called", user=str(session.user))
        assert session.user is not None

        if not self.access_allowed(session.user, lock_required=True):
            return ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied))

        if not self.locked:
            return -1  # RenewLockStatus = E_NotLocked

        if await self.renew_lock(session.user):
            return 0  # RenewLockStatus = OK

        return ua.StatusCode(ua.UInt32(StatusCodes.BadNothingToDo))

    @property
    def max_inactive_time_ms(self) -> int:
        """Maximum time in milliseconds a lock owner is allowed to be inactive."""
        return int(self.server.config.access_control.max_inactive_lock_time_milliseconds)

    @property
    def locked(self) -> bool:
        """Whether this ``Lock`` is locked."""
        return self._locking_user is not None

    @property
    def locking_user(self) -> UserAuthorization | None:
        """``User`` instance owning this ``Lock`` or ``None`` if unlocked."""
        return self._locking_user
