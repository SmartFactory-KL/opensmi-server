# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixins for both providing their own or forwarding to the parent OPC UA logger."""

from collections.abc import AsyncGenerator

from opensmi.core.lifecycle_mixin import lifecycle
from typing_extensions import override

from opensmi.server.interfaces import AbstractUaLogger, AbstractUaObject
from opensmi.server.notification import Notification
from opensmi.server.ua_object import UaObject


class NotificationMixin(UaObject, AbstractUaLogger):
    """Mixin which implements the `AbstractUaLogger` interface using their own `Notification` via composition."""

    __notification: Notification | None = None

    @lifecycle
    async def lifecycle_notification(self) -> AsyncGenerator[None]:
        """Initialize the notification."""
        self.__notification = Notification()
        self.__notification.parent = self  # pyright: ignore[reportAttributeAccessIssue]
        await self.__notification.ua_create_node(self.ua_node, exist_ok=True)
        await self.__notification.init()

        yield
        # no shutdown

    @property
    def notification(self) -> Notification:
        """Return the notification for this object."""
        assert self.__notification is not None, "Notification was not initialized!"
        return self.__notification

    @override
    async def ua_log_info(self, msg: str, *, severity: int = 0, source: AbstractUaObject | None = None) -> None:
        await self.notification.ua_log_info(msg, severity=severity, source=source)

    @override
    async def ua_log_warning(self, msg: str, *, severity: int = 555, source: AbstractUaObject | None = None) -> None:
        await self.notification.ua_log_warning(msg, severity=severity, source=source)

    @override
    async def ua_log_error(
        self, msg: str, *, severity: int = 999, code: str = "", source: AbstractUaObject | None = None
    ) -> None:
        await self.notification.ua_log_error(msg, severity=severity, code=code, source=source)


class NotificationForwarderMixin(AbstractUaObject, AbstractUaLogger):
    """Mixin which implements the `AbstractUaLogger` interface by forwarding the logging messages to the parent.

    Note: The parent **must** implement `AbstractUaLogger` interface.
    """

    @override
    async def ua_log_info(self, msg: str, *, severity: int = 0, source: AbstractUaObject | None = None) -> None:
        parent = getattr(self, "parent", None)
        assert isinstance(parent, AbstractUaLogger), f"parent {parent} of {self} does not implement an OPC UA logger!"
        await parent.ua_log_info(msg=msg, severity=severity, source=self)

    @override
    async def ua_log_warning(self, msg: str, *, severity: int = 555, source: AbstractUaObject | None = None) -> None:
        parent = getattr(self, "parent", None)
        assert isinstance(parent, AbstractUaLogger), f"parent {parent} of {self} does not implement an OPC UA logger!"
        await parent.ua_log_warning(msg=msg, severity=severity, source=self)

    @override
    async def ua_log_error(
        self, msg: str, *, severity: int = 999, code: str = "", source: AbstractUaObject | None = None
    ) -> None:
        parent = getattr(self, "parent", None)
        assert isinstance(parent, AbstractUaLogger), f"parent {parent} of {self} does not implement an OPC UA logger!"
        await parent.ua_log_error(msg=msg, severity=severity, code=code, source=self)
