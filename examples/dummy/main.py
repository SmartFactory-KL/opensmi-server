# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio

from dummy_machine import DummyMachine

from opensmi.server import Server


async def main() -> None:
    async with Server(config_path="config.toml") as server:  # will properly shut down the server
        await server.add_machine(DummyMachine())  # add machine(s)
        await server.start(blocking=True)  # start the server and block while it is running


if __name__ == "__main__":
    asyncio.run(main())
