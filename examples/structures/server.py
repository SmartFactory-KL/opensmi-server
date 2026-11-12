# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

"""Example to demonstrate structured values defined as dataclasses (``opensmi.server.structures``).

A machine publishes the current workpiece, which nests its dimensions, and a list of measurements. The server
changes the workpiece once after it has started. Run ``client.py`` against it.
"""

import asyncio
from dataclasses import dataclass, field

from asyncua import ua

from opensmi.server import BaseMachine, ParameterSet, Server, UaVariable
from opensmi.server.mixins import ParameterSetMixin


@dataclass
class Dimensions:
    """Size of a workpiece in mm."""

    length: float = 0.0
    width: float = 0.0
    height: float = 0.0


@dataclass
class Workpiece:
    """A workpiece with its dimensions."""

    id: str = ""
    material: str = ""
    dimensions: Dimensions = field(default_factory=Dimensions)


@dataclass
class Measurement:
    """One measured value and whether it is within tolerance."""

    name: str = ""
    value: float = 0.0
    in_tolerance: bool = True


class ExampleParameterSet(ParameterSet):
    """Parameters of the example machine."""

    # The structure types are created on the server when the variables are created
    # bypass_lock=True allows write-access without occupation
    CurrentWorkpiece = UaVariable(
        initial_value=Workpiece("W-001", "steel", Dimensions(120.0, 80.0, 10.0)), writable=True, bypass_lock=True
    )
    """The workpiece the machine is working on."""
    Measurements = UaVariable(initial_value=[Measurement("length", 120.0)], writable=True, bypass_lock=True)
    """The measurements of the current workpiece."""


class ExampleMachine(ParameterSetMixin[ExampleParameterSet], BaseMachine):
    """Machine with a structured parameter and a list of structured measurements."""

    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "en-US")
        )


async def main() -> None:
    """Serve the machine and change its workpiece."""
    async with Server() as server:
        machine = await server.add_machine(ExampleMachine())
        await server.start(blocking=False)

        await machine.parameter_set.CurrentWorkpiece.write(Workpiece("W-002", "aluminium", Dimensions(90.0, 60.0, 5.0)))
        print(f"Serving on {server.config.ua_server.endpoint_address}", flush=True)  # noqa: T201
        await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
