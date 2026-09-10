# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Classes related to OPC UA Relative Spatial Location Information Model.

see https://reference.opcfoundation.org/specs/OPC-10000-210/full
"""

from __future__ import annotations

import contextlib
from collections.abc import AsyncGenerator
from dataclasses import dataclass

from asyncua import ua
from asyncua.common.node import Node
from asyncua.ua.uaerrors import BadNoMatch, BadTypeMismatch
from opensmi.core.lifecycle_mixin import lifecycle
from opensmi.core.ua_node_util import get_node_id, read_unit, read_value, write_unit, write_value
from typing_extensions import override

from opensmi.server.mixins import ParentMixin
from opensmi.server.nodesets import RSLNodeIds
from opensmi.server.ua_object import UaObject, UaObjectDefinition


@dataclass(frozen=True, slots=True)
class Position:
    """Cartesian position defined by X, Y, and Z coordinates.

    see https://reference.opcfoundation.org/specs/OPC-10000-210/b-1
    """

    x: float
    y: float
    z: float
    unit: str = "mm"


@dataclass(frozen=True, slots=True)
class Orientation:
    """Angular orientation defined by A, B, and C angles.

    The angles represent the orientation of the Cartesian frame according
    to the A/B/C convention defined by the OPC UA model.

    see https://reference.opcfoundation.org/specs/OPC-10000-210/b-2
    """

    a: float
    b: float
    c: float
    unit: str = "°"


class CartesianFrame(UaObject, ParentMixin[UaObject]):
    """Describes the translation and rotation of an object relative to a base coordinate frame.

    The position is represented by the Cartesian coordinates X, Y, and Z.
    The orientation is represented by the angles A, B, and C, corresponding
    to roll, pitch, and yaw respectively.

    See also ``CartesianFrameAngleOrientationType`` from OPC UA Part 210: Relative Spacial Location specification.
    https://reference.opcfoundation.org/specs/OPC-10000-210/7.3
    """

    _ua_position: Node
    _ua_orientation: Node
    _ua_base: Node

    _ua_position_x: Node
    _ua_position_y: Node
    _ua_position_z: Node

    _ua_orientation_a: Node
    _ua_orientation_b: Node
    _ua_orientation_c: Node

    def __init__(
        self,
        *,
        name: str,
        position: Position,
        orientation: Orientation,
        base: CartesianFrame | None,
        **kwargs,
    ) -> None:
        """Initialize a Cartesian frame with position and orientation.

        :param name: Name of the frame.
        :param position: Translation of the frame relative to the base frame, represented by X, Y, and Z.
        :param orientation: Orientation of the frame relative to the base frame, represented by
            A (roll about X), B (pitch about Y), and C (yaw about Z).
        :param base: Optional Node ID of the base frame relative to which this frame is specified.
        """
        super().__init__(name=name, **kwargs)
        self._name = name
        self._position: Position = position
        self._orientation: Orientation = orientation
        self._base: CartesianFrame | None = base

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=RSLNodeIds.CartesianFrameAngleOrientationType, namespace_uri=RSLNodeIds)

    @lifecycle
    async def lifecycle_frame(self) -> AsyncGenerator[None]:
        """Initialize the Cartesian frame."""
        ns = self.server.ua_get_namespace_index(RSLNodeIds)
        browse_name = ua.QualifiedName("Base", NamespaceIndex=ns)
        try:
            self._ua_base = await self.ua_node.get_child(browse_name)
        except BadNoMatch:
            # TODO(CaHa): asyncua does not instantiate the mandatory base for some reason
            node_id = get_node_id(self.ua_node, name=browse_name)
            self._ua_base = await self.ua_node.add_variable(
                node_id, browse_name, ua.Variant(VariantType=ua.VariantType.NodeId)
            )

        self._ua_position = await self.ua_node.get_child(f"{ns}:Position")
        self._ua_position_x = await self._ua_position.get_child("X")
        self._ua_position_y = await self._ua_position.get_child("Y")
        self._ua_position_z = await self._ua_position.get_child("Z")

        self._ua_orientation = await self.ua_node.get_child(f"{ns}:Orientation")
        self._ua_orientation_a = await self._ua_orientation.get_child(f"{ns}:A")
        self._ua_orientation_b = await self._ua_orientation.get_child(f"{ns}:B")
        self._ua_orientation_c = await self._ua_orientation.get_child(f"{ns}:C")

        await self.write_base(self._base)
        await self.write_position(self._position)
        await self.write_orientation(self._orientation)

        yield
        # no shutdown

    async def write_base(self, base: CartesianFrame | None) -> None:
        """Write the base coordinate frame reference to the OPC UA server."""
        if base is None:
            with contextlib.suppress(BadTypeMismatch):  # WorldFrame base is Null variant, so we don't need to write
                await write_value(self._ua_base, None)
        else:
            assert isinstance(base, CartesianFrame), f"base {base.__class__.__name__} is no instance of CartesianFrame!"
            await write_value(self._ua_base, base.ua_node.nodeid)

    async def read_base(self) -> CartesianFrame | None:
        """Read the base coordinate frame reference from the OPC UA server."""
        node_id: ua.NodeId | None = await read_value(self._ua_base)
        if node_id is None:
            return None
        obj = self.server.get_ua_object(node_id)
        assert isinstance(obj, CartesianFrame), f"base {obj.__class__.__name__} is no instance of CartesianFrame!"
        return obj

    async def write_position(self, position: Position) -> None:
        """Write the given Cartesian position to the OPC UA server."""
        await self._ua_position.write_value(ua.ThreeDCartesianCoordinates(X=position.x, Y=position.y, Z=position.z))  # type: ignore
        await self._ua_position_x.write_value(ua.Double(position.x))
        await self._ua_position_y.write_value(ua.Double(position.y))
        await self._ua_position_z.write_value(ua.Double(position.z))
        await write_unit(self._ua_position, self._position.unit, engineering_unit="LengthUnit")
        self.logger.debug("Written position", position=position)

    async def read_position(self) -> Position:
        """Read the Cartesian position from the OPC UA server."""
        x = await self._ua_position_x.read_value()
        y = await self._ua_position_y.read_value()
        z = await self._ua_position_z.read_value()
        unit = await read_unit(self._ua_position, engineering_unit="LengthUnit")

        return Position(x=float(x), y=float(y), z=float(z), unit=unit.DisplayName.Text)

    async def write_orientation(self, orientation: Orientation) -> None:
        """Write given angular orientation to the OPC UA server."""
        await self._ua_orientation.write_value(ua.ThreeDOrientation(A=orientation.a, B=orientation.b, C=orientation.c))  # type: ignore
        await self._ua_orientation_a.write_value(ua.Double(orientation.a))
        await self._ua_orientation_b.write_value(ua.Double(orientation.b))
        await self._ua_orientation_c.write_value(ua.Double(orientation.c))
        await write_unit(self._ua_orientation, self._orientation.unit, engineering_unit="AngleUnit")
        self.logger.debug("Written orientation", orientation=orientation)

    async def read_orientation(self) -> Orientation:
        """Read the angular orientation from the OPC UA server."""
        a = await self._ua_orientation_a.read_value()
        b = await self._ua_orientation_b.read_value()
        c = await self._ua_orientation_c.read_value()
        unit = await read_unit(self._ua_orientation, engineering_unit="AngleUnit")

        return Orientation(a=float(a), b=float(b), c=float(c), unit=unit.DisplayName.Text)


class PositionFrame(CartesianFrame):
    """Cartesian position frame with the fixed OPC UA name ``PositionFrame``.

    A position frame defines a Cartesian frame relative to a required base coordinate frame.
    """

    def __init__(self, position: Position, orientation: Orientation, base: CartesianFrame, **kwargs) -> None:
        """*Cooperative* constructor."""
        super().__init__(name="PositionFrame", position=position, orientation=orientation, base=base, **kwargs)


class WorldFrame(CartesianFrame):
    """Root Cartesian frame with the fixed OPC UA name ``WorldFrame``.

    The world frame defines a Cartesian frame without a base coordinate frame.
    """

    def __init__(self, position: Position, orientation: Orientation, **kwargs) -> None:
        """*Cooperative* constructor."""
        super().__init__(name="WorldFrame", position=position, orientation=orientation, base=None, **kwargs)


class SpatialObject(UaObject, ParentMixin[UaObject]):
    """Spatial Object contains a `PositionFrame` and further `CartesianFrame` can be attached.

    See OPC UA Relative Spatial Location specification.
    """

    def __init__(self, *, name: str | None = "SpatialObject", position_frame: PositionFrame, **kwargs) -> None:
        """*Cooperative* constructor."""
        super().__init__(name=name, **kwargs)
        assert position_frame is not None, "position_frame cannot be None!"

        self._position_frame: PositionFrame = position_frame
        self._ua_attach_points: Node | None = None
        self._attach_points: set[CartesianFrame] = set()

    @lifecycle
    async def lifecycle_spatial_object(self) -> AsyncGenerator[None]:
        """Initialize the spatial object."""
        self._position_frame.parent = self
        await self._position_frame.ua_create_node(self.ua_node, remove_existing=True)
        await self._position_frame.init()

        yield

        # no shutdown

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=RSLNodeIds.SpatialObjectType)

    @property
    def position_frame(self) -> PositionFrame:
        """Return the position frame of the spatial object."""
        return self._position_frame

    async def add_attach_point(self, attach_point: CartesianFrame) -> None:
        """Add given cartesian frame to the spatial object."""
        assert attach_point is not None, "Given attach_point cannot be None!"
        assert attach_point.is_initialized, "Given attach_point must be be initialized!"
        assert attach_point not in self._attach_points, "Given attach_point already added!"

        ns = self.server.ua_get_namespace_index(RSLNodeIds)

        if self._ua_attach_points is None:
            self._ua_attach_points = await self.ua_node.add_folder(
                nodeid=ua.NodeId(
                    ua.String(f"{self.ua_node.nodeid.Identifier}.AttachPoints"),
                    self.ua_node.nodeid.NamespaceIndex,
                ),
                bname=ua.QualifiedName("AttachPoints", ns),
            )
            assert self._ua_attach_points is not None

        attach_point.parent = self
        await attach_point.ua_create_node(self._ua_attach_points)
        await attach_point.init()
        self._attach_points.add(attach_point)
        self.logger.info("Added AttachPoint", attach_point=attach_point)

    @property
    def attach_points(self) -> set[CartesianFrame]:
        """Return all attached cartesian frames."""
        return self._attach_points


class SpatialObjectList(UaObject, ParentMixin[UaObject]):
    """Spatial Object List (SOL) from Relative Spatial Location specification."""

    def __init__(self, world_frame: WorldFrame, parent: UaObject, **kwargs) -> None:
        """*Cooperative* constructor."""
        super().__init__(name="MachineRoomList", **kwargs)
        assert world_frame is not None, "world_frame cannot be None!"

        self.parent = parent
        self._world_frame: WorldFrame = world_frame
        self._spatial_objects: set[SpatialObject] = set()

    @lifecycle
    async def lifecycle_spatial_object_list(self) -> AsyncGenerator[None]:
        """Initialize the spatial object list."""
        self._world_frame.parent = self
        await self._world_frame.ua_create_node(self.ua_node, instantiate_optional=True, remove_existing=True)
        await self._world_frame.init()

        ns = self.server.ua_get_namespace_index(RSLNodeIds)
        ua_identifier = await self.ua_node.get_child(f"{ns}:Identifier")
        await ua_identifier.write_value(f"{self.parent.name}SpatialObjectList")

        yield
        # no shutdown

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=RSLNodeIds.SpatialObjectsListType)

    async def add_spatial_object(self, spatial_object: SpatialObject) -> None:
        """Add given SpatialObject to the spatial object list."""
        assert spatial_object is not None, "Given spatial_object cannot be None!"
        assert spatial_object.is_initialized, "Given spatial_object must be initialized!"
        assert spatial_object not in self._spatial_objects, "Given spatial_object already added!"

        self._spatial_objects.add(spatial_object)
        await self.ua_node.add_reference(spatial_object.ua_node, reftype=ua.object_ids.ObjectIds.Organizes)
        self.logger.info("Added Spatial Object", spatial_object=spatial_object)

    @property
    def world_frame(self) -> WorldFrame:
        """Return the world frame of the spatial object list."""
        return self._world_frame

    @property
    def spatial_objects(self) -> set[SpatialObject]:
        """Return all spacial objects."""
        return self._spatial_objects
