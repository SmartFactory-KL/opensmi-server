# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixin for providing a `Lock` to an `UaObject`."""

from collections.abc import AsyncGenerator

from open_smi_common.lifecycle_mixin import lifecycle
from open_smi_server.lock import Lock
from open_smi_server.ua_object import UaObject


class LockableMixin:
    """Mixin for providing a `Lock` to an `UaObject`."""

    __lock: Lock | None = None

    @lifecycle
    async def lifecycle_lock(self) -> AsyncGenerator[None]:
        """Initialize the `Lock` for an `UaObject`."""
        self.lock.parent = self  # pyright: ignore[reportAttributeAccessIssue]
        await self.lock.ua_create_node(self.ua_node, exist_ok=True)  # pyright: ignore[reportAttributeAccessIssue]
        await self.lock.init()

        yield

        await self.lock.shutdown()

    @property
    def lock(self) -> Lock:
        """Return the associated `Lock` for this `UaObject`."""
        if self.__lock is None:
            assert isinstance(self, UaObject)
            self.__lock = Lock(minimum_access_level=self.minimum_access_level)
        return self.__lock
