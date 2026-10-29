# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT
import pytest
from asyncua import ua
from opensmi.core import Unit
from typing_extensions import override

from opensmi.server import BaseMachine
from opensmi.server.mixins import (
    SpatialObjectListMixin,
    SpatialObjectMixin,
)
from opensmi.server.nodesets import RSLNodeIds
from opensmi.server.spatial_object import Orientation, Pose, Position, PositionFrame, SpatialObject


class _TestMachine(SpatialObjectMixin, SpatialObjectListMixin, BaseMachine):
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

        await self._init_spatial_object_list()

        await self._init_spatial_object(
            SpatialObject(
                position_frame=PositionFrame(
                    initial_pose=Pose(position=Position(x=1, y=2, z=3), orientation=Orientation(a=4, b=5, c=6)),
                    initial_base=self.spatial_object_list.world_frame,
                ),
            )
        )


@pytest.mark.asyncio
async def test_frame_folders_are_created_on_first_use(server) -> None:
    async with server:
        machine = _TestMachine()
        await server.add_machine(machine)
        so = machine.spatial_object
        ns = server.ua_get_namespace_index(RSLNodeIds)

        # optional folders are absent until a frame needs them
        names = [(await c.read_browse_name()).Name for c in await so.ua_node.get_children()]
        assert "InternalFrames" not in names
        assert "AlternativeFrames" not in names

        a = await so.add_internal_frame(
            initial_pose=Pose(position=Position(0, 0, 1, unit=Unit.METRE), orientation=Orientation(0, 0, 0)),
            name="a",
        )
        assert a.is_initialized

        b = await so.add_internal_frame(
            initial_pose=Pose(position=Position(0, 0, 2, unit=Unit.METRE), orientation=Orientation(0, 0, 0)),
            initial_base=a,
            name="b",
        )
        assert b.is_initialized

        c = await so.add_alternative_frame(
            initial_pose=Pose(position=Position(1, 0, 0, unit=Unit.METRE), orientation=Orientation(0, 0, 0)),
            name="c",
        )
        assert c.is_initialized

        internal = await so.ua_node.get_child(f"{ns}:InternalFrames")
        assert {(await n.read_browse_name()).Name for n in await internal.get_children()} == {"a", "b"}
        alt = await so.ua_node.get_child(f"{ns}:AlternativeFrames")
        assert [(await n.read_browse_name()).Name for n in await alt.get_children()] == ["c"]
        assert (await b.read_base()) is a  # Base points at the other folder's frame
        assert server.get_ua_object(a.ua_node.nodeid) is a  # registry finds it, nothing else holds it
        assert so.internal_frames._frames == {a, b}
        assert so.alternative_frames._frames == {c}
