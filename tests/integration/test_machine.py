# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT
import pytest
from asyncua import ua

from opensmi.server import BaseMachine


class _TestMachine(BaseMachine):
    __test__ = False

    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )


@pytest.mark.asyncio
async def test_no_machines(server) -> None:
    async with server:
        pass


@pytest.mark.asyncio
async def test_add_single_machines(server) -> None:
    async with server:
        await server.add_machine(_TestMachine())


@pytest.mark.asyncio
async def test_add_multiple_machines(server) -> None:
    async with server:
        await server.add_machine(_TestMachine(name="TestMachine_1"))
        await server.add_machine(_TestMachine(name="TestMachine_2"))
