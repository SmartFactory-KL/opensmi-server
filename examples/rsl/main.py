# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0
import asyncio

from asyncua import ua
from typing_extensions import override

from opensmi.server import BaseComponent, BaseMachine, Server, SpatialObject
from opensmi.server.mixins import (
    MonitoringMixin,
    NotificationForwarderMixin,
    ParentMixin,
    SpatialObjectListMixin,
    SpatialObjectMixin,
)
from opensmi.server.spatial_object import Orientation, Position, PositionFrame


class DummyComponent(
    MonitoringMixin,  # required by spatial object
    SpatialObjectMixin,
    NotificationForwarderMixin,  # forward OPC UA logging to our parent
    ParentMixin["ExampleMachine"],
    BaseComponent,  # base class last
):
    @override
    async def _write_identification(self) -> None:
        # These identification variables must be set for components
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )

    async def _init(self) -> None:
        await super()._init()

        await self._init_spatial_object(
            SpatialObject(
                name="DummySpatialObject",
                position_frame=PositionFrame(
                    position=Position(x=1, y=2, z=3),
                    orientation=Orientation(a=4, b=5, c=6),
                    base=self.parent.spatial_object_list.world_frame,
                ),
            )
        )


class ExampleMachine(
    MonitoringMixin,
    SpatialObjectListMixin,
    BaseMachine,
):
    @override
    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )

    @override
    async def _init(self) -> None:
        await super()._init()

        # Components
        await self.add_component(DummyComponent())


async def main() -> None:
    async with Server() as server:  # will properly shut down the server
        await server.add_machine(ExampleMachine())  # add machine(s)
        await server.start(blocking=True)  # start the server and block while it is running


if __name__ == "__main__":
    asyncio.run(main())
