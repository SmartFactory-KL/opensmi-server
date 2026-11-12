# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

"""Client for ``server.py``: reads the structures with plain asyncua and tries a write of the wrong type.

The client knows nothing about ``Point`` or ``Waypoint``. ``load_data_type_definitions`` builds the Python classes
from the ``DataTypeDefinition`` that the server published.
"""

import asyncio
import logging

from asyncua import Client, ua

URL = "opc.tcp://operator:operator@localhost:4841/server"


async def main() -> None:
    """Read both variables and try a write of the wrong type."""
    async with Client(URL) as client:
        await client.load_data_type_definitions()

        target = client.get_node("ns=1;s=ExampleMachine.ParameterSet.Target")
        waypoint = await target.read_value()
        print(f"Target: {waypoint.name!r} at ({waypoint.position.x}, {waypoint.position.y}, {waypoint.position.z})")  # noqa: T201

        path = client.get_node("ns=1;s=ExampleMachine.ParameterSet.Path")
        points = await path.read_value()
        print(f"Path: {len(points)} point(s), first at x={points[0].x}")  # noqa: T201

        # A Waypoint where an array of Point belongs: the server's write check refuses it
        try:
            await path.write_value(ua.Variant([waypoint], ua.VariantType.ExtensionObject))
        except ua.UaStatusCodeError as err:
            print(f"Writing a Waypoint to Path was refused: {ua.StatusCode(err.code).name}")  # noqa: T201


if __name__ == "__main__":
    logging.getLogger("asyncua").setLevel(logging.ERROR)
    asyncio.run(main())
