# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from asyncua import ua
from typing_extensions import override

from opensmi.server import BaseMachine, Server
from opensmi.server.config import ServerConfiguration


@pytest.fixture
def server() -> Server:
    """Return an uninitialized `Server` instance."""
    config = ServerConfiguration()
    config.ua_server.endpoint_address = "opc.tcp://0.0.0.0:0/server"
    return Server(config=config)


@pytest_asyncio.fixture
async def empty_server(server) -> AsyncGenerator[Server]:
    """Return a started empty `Server` instance."""
    async with server:
        await server.start(blocking=False)
        yield server


class Machine(BaseMachine):
    @override
    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )


@pytest_asyncio.fixture
async def machine(server) -> AsyncGenerator[Machine]:
    machine = Machine()
    async with server:
        await server.add_machine(machine)
        await server.start(blocking=False)
        yield machine
