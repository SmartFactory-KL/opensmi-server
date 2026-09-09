# SPDX-FileCopyrightText: 2026 The Authors
#
# SPDX-License-Identifier: MIT

import asyncio

from dummy.dummy_machine import DummyMachine

from open_smi_server import get_server


async def main() -> None:
    async with get_server() as server:  # will properly shut down the server
        await server.configure("config.toml")
        await server.init()  # initialize it
        await server.add_machine(DummyMachine())  # add machine(s)
        await server.start(blocking=True)  # start the server and block while it is running


if __name__ == "__main__":
    asyncio.run(main())
