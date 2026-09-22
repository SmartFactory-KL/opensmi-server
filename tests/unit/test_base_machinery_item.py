# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import pytest
from asyncua import ua

from opensmi.server import BaseComponent
from opensmi.server.mixins import NotificationMixin


class EmptyComponent(NotificationMixin, BaseComponent):
    async def _init(self) -> None:
        pass

    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("no")
        await self.identification.Manufacturer.write(ua.LocalizedText("Test"))


def test_startup_skill_raises_key_error_in_empty_component() -> None:
    component = EmptyComponent()
    with pytest.raises(KeyError):
        _ = component.startup_skill
