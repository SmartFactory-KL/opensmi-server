# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

"""Client for ``server.py``: reads and writes the structures with plain asyncua and tries a write of the wrong type.

The client knows none of the classes. ``load_data_type_definitions`` builds them from the ``DataTypeDefinition``
that the server published.
"""

import asyncio
import logging

from asyncua import Client, ua

URL = "opc.tcp://operator:operator@localhost:4841/server"


async def main() -> None:
    """Read both variables, add a measurement and try a write of the wrong type."""
    async with Client(URL) as client:
        await client.load_data_type_definitions()

        workpiece_node = client.get_node("ns=1;s=ExampleMachine.ParameterSet.CurrentWorkpiece")
        workpiece = await workpiece_node.read_value()
        size = workpiece.dimensions
        print(f"Workpiece: {workpiece.id} ({workpiece.material}), {size.length} x {size.width} x {size.height} mm")  # noqa: T201

        measurements_node = client.get_node("ns=1;s=ExampleMachine.ParameterSet.Measurements")
        measurements = await measurements_node.read_value()
        print(f"Measurements: {[each.name for each in measurements]}")  # noqa: T201

        # Add a measurement, built from a copy of the first one
        width = type(measurements[0])(name="width", value=size.width, in_tolerance=False)
        await measurements_node.write_value(ua.Variant([*measurements, width], ua.VariantType.ExtensionObject))
        print(f"Measurements now: {[each.name for each in await measurements_node.read_value()]}")  # noqa: T201

        # A workpiece where a list of measurements belongs: the server refuses it
        try:
            await measurements_node.write_value(ua.Variant([workpiece], ua.VariantType.ExtensionObject))
        except ua.UaStatusCodeError as err:
            print(f"Writing a workpiece to Measurements was refused: {ua.StatusCode(err.code).name}")  # noqa: T201


if __name__ == "__main__":
    logging.getLogger("asyncua").setLevel(logging.ERROR)
    asyncio.run(main())
