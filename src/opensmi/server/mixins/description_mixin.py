# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixin for initializing the OPC UA description of `UaObject` using their Python docstrings."""

from abc import ABC
from collections.abc import AsyncGenerator

from opensmi.core.lifecycle_mixin import lifecycle

from opensmi.server.docstrings import description_from_docstring
from opensmi.server.interfaces import AbstractUaObject


class DescriptionMixin(AbstractUaObject, ABC):
    """Mixin for initializing the OPC UA description of `UaObject` using their Python docstrings."""

    @lifecycle
    async def lifecycle_description(self) -> AsyncGenerator[None]:
        """Write the class docstring as OPC UA description."""
        # Note: inspect.getdoc() would also access inherited docstrings, which is unwanted behavior here
        description = description_from_docstring(type(self).__doc__)
        if description:
            await self.write_description(description)
        else:
            # annoy the users so they provide descriptions to their skills, components, etc.
            self.logger.warning("Missing description", path=str(self.path))

        yield
        # no shutdown
