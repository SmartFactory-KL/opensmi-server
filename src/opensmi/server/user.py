# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""User (role), authentication and OPC UA representation."""

from __future__ import annotations

import base64
from collections.abc import AsyncGenerator, Iterator

from asyncua.common.node import Node
from asyncua.crypto.permission_rules import User as UaUser
from asyncua.crypto.permission_rules import UserRole
from asyncua.ua import Int32
from cryptography.exceptions import InvalidKey
from opensmi.core.lifecycle_mixin import lifecycle
from typing_extensions import override

from opensmi.server.common import get_scrypt_instance
from opensmi.server.config import UserConfiguration
from opensmi.server.nodesets import SmartFactoryMachineSetNodeIds
from opensmi.server.protocols import SessionProtocol, UserProtocol
from opensmi.server.ua_object import UaObject, UaObjectDefinition


def encode_password(password: bytes) -> str:
    """Encode given (peppered) password and prefix it with ``base64:``."""
    return f"base64:{base64.b64encode(password).decode()}"


def decode_password(password: str) -> bytes:
    """Decode given encoded (peppered) password."""
    return base64.b64decode(password.removeprefix("base64:"))


class User(UaObject, UserProtocol):
    """User (role), authentication and OPC UA representation."""

    _ua_is_present: Node
    """ OPC UA (dynamic) node indicating whether this user instance is present. """

    _password: bytes | str

    def __init__(self, *, config: UserConfiguration) -> None:
        """Construct new user based on given configuration."""
        super().__init__(name=config.name)
        if config.password.startswith("base64:"):
            self._password = decode_password(config.password)
        else:
            self._password = config.password

        self._priority: int = config.priority
        self._maximum_access_level: int = config.maximum_access_level
        self._current_access_level: int = config.maximum_access_level
        self.allow_multiple: bool = config.allow_multiple
        self._sessions: set[SessionProtocol] = set()

    @property
    @override
    def current_access_level(self) -> int:
        return self._current_access_level

    @current_access_level.setter
    def current_access_level(self, access_level: int) -> None:
        access_level = max(0, int(access_level))
        if access_level > self.maximum_access_level:
            msg = f"Given access level must be <= {self.maximum_access_level} for user '{self.name}'!"
            raise ValueError(msg)
        old_level = self._current_access_level
        self._current_access_level = access_level
        self.logger.info("Updated current access level", new_level=access_level, old_level=old_level)

    @property
    @override
    def maximum_access_level(self) -> int:
        return self._maximum_access_level

    @property
    @override
    def priority(self) -> int:
        return self._priority

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=SmartFactoryMachineSetNodeIds.UserType)

    @override
    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield "name", self.name
        yield "access_level", f"{self.current_access_level}/{self.maximum_access_level}"
        yield "no_of_sessions", len(self._sessions)

    @lifecycle
    async def lifecycle_ua_user(self) -> AsyncGenerator[None]:
        """Initialize OPC UA representation."""
        for child in await self.ua_node.get_children():
            bname = await child.read_browse_name()
            if bname.Name in ["Name", "UserRole"]:
                await child.write_value(self.name)
            elif bname.Name == "AllowMultiple":
                await child.write_value(self.allow_multiple)
            elif bname.Name == "UserLevel":
                await child.write_value(str(self.priority))
            elif bname.Name == "MaxAccessLevel":
                await child.write_value(Int32(self.maximum_access_level))
            elif bname.Name == "IsPresent":
                self._ua_is_present = child
                await self._ua_is_present.write_value(False)
            elif bname.Name == "Language":
                await child.write_value("en")
            # TODO(CaHa): ID, CardUid
        assert self._ua_is_present is not None
        yield
        # no shutdown

    @property
    @override
    def role(self) -> UserRole:
        return UserRole.User

    @property
    @override
    def present(self) -> bool:
        return any(session.is_activated for session in self._sessions)

    @property
    @override
    def sessions(self) -> set[SessionProtocol]:
        return set(self._sessions)

    @override
    async def add_session(self, session: SessionProtocol) -> None:
        self.logger.debug("Adding session", session=session.name, sessions=len(self._sessions))
        self._sessions.add(session)
        if self.is_initialized:
            await self._ua_is_present.write_value(True)

    @override
    async def remove_session(self, session: SessionProtocol) -> None:
        self.logger.debug("Removing session", session=session.name, sessions=len(self._sessions))
        self._sessions.discard(session)
        if self.is_initialized and len(self._sessions) == 0:
            await self._ua_is_present.write_value(False)

    @override
    def check_password(self, password: bytes | str | None) -> bool:
        if password is None:
            return False

        if isinstance(self._password, str):
            self.logger.warning("Using unencrypted password!")

        if isinstance(self._password, bytes):
            if isinstance(password, str):
                password = password.encode()
            try:
                get_scrypt_instance().verify(password, self._password)
                return True  # noqa: TRY300
            except InvalidKey:
                return False

        if isinstance(password, bytes):
            password = password.decode()

        return self._password == password  # clear text (e.g. for test cases)


INTERNAL_USER = UaUser(name="INTERNAL", role=UserRole.Admin)
