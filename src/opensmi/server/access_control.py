# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Client authentication & authorization."""

from __future__ import annotations

import asyncio
import inspect
from asyncio import Task
from dataclasses import dataclass
from inspect import currentframe
from types import MappingProxyType
from typing import TYPE_CHECKING, cast

import structlog
from asyncua import ua
from asyncua.crypto.permission_rules import UserRole
from asyncua.server.address_space import AddressSpace, AttributeService, MethodService
from asyncua.server.internal_session import InternalSession, SessionState
from asyncua.server.user_managers import UserManager
from asyncua.ua.status_codes import StatusCodes
from opensmi.core import AsyncTaskMixin
from opensmi.core.errors import OutOfRangeError
from opensmi.core.signal import Signal
from typing_extensions import override

from opensmi.server.protocols import SessionProtocol, UserAuthorization, UserProtocol
from opensmi.server.ua_variable import UaVariable
from opensmi.server.user import User

from .ua_object_containers import Users

if TYPE_CHECKING:
    from asyncua.server.internal_server import InternalServer

    from ._server import Server
    from .lock import Lock as Lock


class AccessControl(UserManager, AsyncTaskMixin):
    """Handles user authentication."""

    def __init__(self, *, server: Server) -> None:
        """Construct new instance using ``server``."""
        super().__init__()

        self.logger: structlog.stdlib.BoundLogger = structlog.getLogger("open_smi.AccessControl")
        self._server: Server = server
        self._sessions: list[SessionProtocol] = []
        self.minimum_access: int = int(self._server.config.access_control.minimum_access_level)
        self._lock: asyncio.Lock = asyncio.Lock()

        self._users: dict[str, User] = {
            config.name: User(config=config) for config in server.config.access_control.users
        }

        self.on_session_accepted = Signal[SessionProtocol]()
        self.on_session_closed = Signal[SessionProtocol]()

        self.running: bool = True
        self._task_check_closed_session: Task[None] = asyncio.create_task(
            self._check_for_closed_sessions_loop(), name="AccessControl_check_for_closed_sessions"
        )

    @property
    def users(self) -> MappingProxyType[str, UserProtocol]:
        """Read-only mapping of users."""
        return MappingProxyType(self._users)

    @property
    def sessions(self) -> tuple[SessionProtocol, ...]:
        """Read-only sequence of sessions."""
        return tuple(self._sessions)

    async def init_ua_users(self, users: Users) -> None:
        """Initialize OPC UA representation of all users."""
        self.logger.debug("Initializing UA users...", users=users)
        for user in self._users.values():
            user.parent = users
            await user.ua_create_node(users.ua_node, instantiate_optional=True)
            await user.init()

    async def _add_session(self, session: SessionProtocol) -> None:
        if isinstance(session.user, User):
            user: User = session.user
            await user.add_session(session)
        else:
            self.logger.warning("Unknown session user!", user=session.user)
        self._sessions.append(session)
        await self.on_session_accepted.send(session)

    async def _remove_session(self, session: SessionProtocol) -> None:
        self.logger.debug("Removing session", username=session.user, client=session.name)
        if isinstance(session.user, User):
            user: User = session.user
            await user.remove_session(session)
        self._sessions.remove(session)
        await self.on_session_closed.send(session)

    async def _check_for_closed_sessions_loop(self, *, interval: float = 1) -> None:
        """Continuously check for closed sessions while running.

        :param interval: Time to sleep between check in seconds.
        """
        while self.running:
            async with self._lock:
                remove_list = [session for session in self._sessions if session.state == SessionState.Closed]

                for session in remove_list:
                    await self._remove_session(session)

            await asyncio.sleep(interval)

    @override
    def get_user(
        self,
        iserver: InternalServer,  # pyright: ignore[reportIncompatibleMethodOverride]
        username: str | None = None,
        password: str | bytes | None = None,
        certificate: bytes | None = None,
    ) -> User | None:
        """Check whether given user credentials are correct and allowed to log in.

        :return: User instance if user is allowed to log in, None otherwise.
        """
        if username is None or password is None:
            self.logger.warning("Received invalid username and/or password!")
            return None

        # grab the session via inspection, this method is called by the corresponding internal session
        session = currentframe().f_back.f_locals["self"]  # pyright: ignore[reportOptionalMemberAccess, reportAny]
        if not isinstance(session, InternalSession):
            msg = (
                "Could not grab internal session, it's a very likely asyncua update broke the dirty hack. Please "
                "notify the maintainer of the OpenSMI library!"
            )
            raise TypeError(msg)

        logger = self.logger.bind(user=username, session_name=session.name)

        try:
            user = self._users[username]
        except KeyError:
            logger.warning("Access denied, unknown username!")
            return None

        logger = logger.bind(user=str(user))

        if not user.check_password(password):
            logger.warning("Access denied, wrong password!")
            return None

        # new connection
        if user.allow_multiple or not user.present:
            self._create_task(
                self._add_session(session),  # pyright: ignore[reportArgumentType]
                name="AccessControl_add_session",
            )
            logger.info("Access granted!")
            return user

        # check for a reconnection attempt of already logged-in user (thx to FeDi)
        if self._server.config.access_control.allow_reconnection_from_same_host:
            for user_session in user.sessions:
                # ensure if IP-addresses are equal AND the username is equal to the one of that specific IP-Address
                if (user_session.name[0] == session.name[0]) and (user_session.user.name == username):
                    self._create_task(user_session.close_session(), name="AccessControl_close_session")
                    self._create_task(
                        self._add_session(session),  # pyright: ignore[reportArgumentType]
                        name="AccessControl_add_session",
                    )
                    logger.info("Access granted (again)!")
                    return user

        logger.warning("Access denied, already logged in!")
        return None


class AccessControlAttributeService(AttributeService):
    """AccessControl based AttributeService for OPC-UA.

    Requires a user to obtain exclusive access right the machine/component.
    Note that most variables are read-only, see OPC-UA write masks.
    """

    def __init__(self, *, aspace: AddressSpace, server: Server):
        """Construct a new instance."""
        super().__init__(aspace)
        self._server: Server = server
        self._access_control: AccessControl = self._server.access_control

    @override
    async def write(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        params: ua.WriteParameters,
        user: UserAuthorization | None = None,
    ) -> list[ua.StatusCode]:
        result: list[ua.StatusCode] = []
        for ua_write_value in params.NodesToWrite:
            # we care only about value writes
            if ua_write_value.AttributeId == ua.AttributeIds.Value:  # pyright: ignore[reportPrivateImportUsage]
                try:
                    variable = cast(UaVariable, self._server.get_ua_object(ua_write_value.NodeId))
                    if not isinstance(variable, UaVariable):
                        self._access_control.logger.warning("Access denied, not a variable!", ua_object=variable)
                        result.append(ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied)))
                        continue

                    if not user or not variable.access_allowed(user):
                        result.append(ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied)))
                        continue

                    try:
                        value = ua_write_value.Value.Value.Value
                        variable.write_check(value)
                    except OutOfRangeError:
                        result.append(ua.StatusCode(ua.UInt32(StatusCodes.BadOutOfRange)))
                        continue

                except KeyError:
                    # only allow internal writes to non UaVariable mapped nodes
                    if not user or user.role != UserRole.Admin:
                        result.append(ua.StatusCode(ua.UInt32(StatusCodes.BadUserAccessDenied)))
                        continue

            # no failed checks? use default asyncua behavior:
            result_super: list[ua.StatusCode] = await super().write(ua.WriteParameters([ua_write_value]), user)  # pyright: ignore[reportArgumentType]
            result.extend(result_super)

        return result


@dataclass
class SessionUaVariant:
    """Dummy ``UaVariant`` containing the session for OPC UA method call callbacks."""

    Value: InternalSession


class AccessControlMethodService(MethodService):
    """OPC UA Method Server set implementation that provides session to method call callbacks."""

    @override
    async def _run_method(self, func, parent, *args) -> None:  # noqa: ANN001
        # grab the session via inspection
        session = currentframe().f_back.f_back.f_back.f_locals["self"]  # pyright: ignore[reportOptionalMemberAccess]
        if not isinstance(session, InternalSession):
            msg = (
                "Could not grab internal session, it's a very likely asyncua update broke the dirty hack. "
                "Please notify the maintainer of the OpenSMI library!"
            )
            raise TypeError(msg)

        assert inspect.iscoroutinefunction(func)  # we only use coroutines
        return await func(parent, SessionUaVariant(session), *args)  # pyright: ignore[reportAny]
