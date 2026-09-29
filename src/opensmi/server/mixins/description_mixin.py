# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixin for initializing the OPC UA description of `UaObject` using their Python docstrings."""

import inspect
from abc import ABC
from collections.abc import AsyncGenerator

from opensmi.core.lifecycle_mixin import lifecycle

from opensmi.server.interfaces import AbstractUaObject


class DescriptionMixin(AbstractUaObject, ABC):
    """Mixin for initializing the OPC UA description of `UaObject` using their Python docstrings."""

    @lifecycle
    async def lifecycle_description(self) -> AsyncGenerator[None]:
        """Initialize the `Lock` for an `UaObject`."""
        # Note: inspect.getdoc() would also access inherited docstrings, which is unwanted behavior here
        description: str | None = inspect.cleandoc(type(self).__doc__ or "") or None
        if description:
            await self.write_description(description)
        else:
            # annoy the users so they provide descriptions to their skills, components, etc.
            self.logger.warning("Missing description", path=str(self.path))

        yield
        # no shutdown
