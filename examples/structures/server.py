# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

"""Example to demonstrate structure DataTypes minted without a nodeset (``opensmi.server.structures``).

It mints ``Point`` and ``Waypoint`` (which nests a ``Point``), publishes one ``Waypoint`` and an array of
``Point`` as parameters of a machine, and changes the waypoint once after the server has started.
Run ``client.py`` against it.
"""

import asyncio

from asyncua import ua

from opensmi.server import BaseMachine, ParameterSet, Server, UaVariable
from opensmi.server.mixins import ParameterSetMixin
from opensmi.server.structures import (
    StructureDefinition,
    StructureField,
    encode,
    registry_for,
    structure_array_write_check,
    structure_node_id,
)

URI = "urn:example:structures"

DOUBLE = ua.VariantType.Double

POINT = StructureDefinition(
    type_name="Point",
    fields=tuple(StructureField(name=name, variant_type=DOUBLE) for name in "xyz"),
)
WAYPOINT = StructureDefinition(
    type_name="Waypoint",
    fields=(
        StructureField(name="name", variant_type=ua.VariantType.String),
        StructureField(name="position", variant_type=ua.VariantType.ExtensionObject, datatype=POINT),
    ),
)


class ExampleMachine(ParameterSetMixin[ParameterSet], BaseMachine):
    """Machine that gets two structured parameters in ``main``."""

    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )


async def main() -> None:
    """Mint the structures, publish the parameters and serve."""
    async with Server() as server:
        ns = await server.ua_server.register_namespace(URI)
        await registry_for(server.ua_server).mint(server.ua_server, ns, WAYPOINT)  # mints Point first
        machine = await server.add_machine(ExampleMachine())

        # The NodeIds of the structures contain the namespace index, so the variables are added once it is known
        origin = {"x": 0.0, "y": 0.0, "z": 0.0}
        target = UaVariable(
            encode(WAYPOINT, {"name": "dock", "position": origin}, ns),
            name="Target",
            ua_data_type=structure_node_id("Waypoint", ns),
            writable=True,
            bypass_lock=True,  # a client can write without taking the machine lock first
            description="The waypoint the machine drives to.",
        )
        path = UaVariable(
            [encode(POINT, origin, ns)],
            name="Path",
            ua_data_type=structure_node_id("Point", ns),
            ua_value_rank=ua.ValueRank.OneDimension,
            writable=True,
            bypass_lock=True,
            # asyncua accepts any ExtensionObject in any variable; this keeps other values out
            write_check_callback=structure_array_write_check(POINT, ns),
            description="The points of the path, in order.",
        )
        await machine.parameter_set.add(target)
        await machine.parameter_set.add(path)
        await server.start(blocking=False)

        # Updating a structure is writing a newly encoded value
        await target.write(encode(WAYPOINT, {"name": "bay 3", "position": {"x": 1.5, "y": -2.0, "z": 0.25}}, ns))
        print(f"Serving {URI!r} on {server.config.ua_server.endpoint_address}", flush=True)  # noqa: T201
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
