# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Classes related to OPC UA Relative Spatial Location Information Model.

see https://reference.opcfoundation.org/specs/OPC-10000-210/full
"""

from __future__ import annotations

import asyncio
import contextlib
import math
from collections.abc import AsyncGenerator
from dataclasses import dataclass, replace
from typing import Final

from asyncua import ua
from asyncua.common.node import Node
from asyncua.ua.uaerrors import BadNoMatch, BadTypeMismatch
from opensmi.core import Unit
from opensmi.core.lifecycle_mixin import lifecycle
from opensmi.core.ua_node_util import get_node_id, read_value, write_unit, write_value
from typing_extensions import override

from opensmi.server.mixins import ParentMixin
from opensmi.server.nodesets import RSLNodeIds
from opensmi.server.ua_object import UaObject, UaObjectDefinition

# TODO(CaHa): Maybe attach conversion factor to Unit. UNECE table has this information
_LENGTH: Final = {
    Unit.MILLIMETRE: 0.001,
    Unit.CENTIMETRE: 0.01,
    Unit.METRE: 1.0,
}
_ANGLE: Final = {
    Unit.DEGREE_UNIT_OF_ANGLE: math.pi / 180,
    Unit.RADIAN: 1.0,
}


def _convert(value: float, src_unit: Unit, dst_unit: Unit, table: dict[Unit, float]) -> float:
    if src_unit == dst_unit:
        return value
    try:
        return value * table[src_unit] / table[dst_unit]
    except KeyError as e:
        msg = f"unsupported unit {e.args[0]!r}, expected one of {table}"
        raise ValueError(msg) from None


@dataclass(frozen=True, slots=True)
class Position:
    """Cartesian position defined by X, Y, and Z coordinates.

    see https://reference.opcfoundation.org/specs/OPC-10000-210/b-1
    """

    x: float = 0.0
    y: float = 0.0
    z: float = 0.0
    unit: Unit = Unit.MILLIMETRE

    def to(self, unit: Unit) -> Position:
        """Convert this position to one with given ``unit``."""
        if unit == self.unit:
            return self

        def f(value: float) -> float:
            return _convert(value, self.unit, unit, _LENGTH)

        return Position(f(self.x), f(self.y), f(self.z), unit)

    def __add__(self, other: Position) -> Position:
        """Add two positions together keeping the left operand's unit."""
        other = other.to(self.unit)
        return Position(self.x + other.x, self.y + other.y, self.z + other.z, self.unit)


@dataclass(frozen=True, slots=True)
class Orientation:
    """Angular orientation defined by A=roll (X), B=pitch (Y), C=yaw (Z).

    The rotations are applied as intrinsic ``z-y'-x''`` (yaw, then pitch, then roll),
    which is equivalent to extrinsic ``x-y-z``.

    see https://reference.opcfoundation.org/specs/OPC-10000-210/b-2
    """

    a: float = 0.0
    """Rotation around x axis (roll)"""
    b: float = 0.0
    """Rotation around y axis (pitch)"""
    c: float = 0.0
    """Rotation around z axis (yaw)"""
    unit: Unit = Unit.DEGREE_UNIT_OF_ANGLE

    def to(self, unit: Unit) -> Orientation:
        """Convert this orientation to one with given ``unit``."""
        if unit == self.unit:
            return self

        def f(value: float) -> float:
            return _convert(value, self.unit, unit, _ANGLE)

        return Orientation(f(self.a), f(self.b), f(self.c), unit)


@dataclass(frozen=True, slots=True)
class Pose:
    """Pose defined by Cartesian `Position` and angular `Orientation`.

    Corresponds to OPC UA data type ``3DFrameType``.
    """

    position: Position = Position()
    orientation: Orientation = Orientation()

    def with_position(self, position: Position) -> Pose:
        """Return a copy of this Pose with given ``position`` keeping the orientation."""
        return replace(self, position=position)

    def with_orientation(self, orientation: Orientation) -> Pose:
        """Return a copy of this Pose with given ``orientation`` keeping the position."""
        return replace(self, orientation=orientation)

    def to_ua(self, *, length_unit: Unit, angle_unit: Unit) -> ua.ThreeDFrame:
        """Convert to a OPC UA ``3DFrame``, expressed in the given units."""
        p, o = self.position.to(length_unit), self.orientation.to(angle_unit)
        return ua.ThreeDFrame(
            CartesianCoordinates=ua.ThreeDCartesianCoordinates(X=p.x, Y=p.y, Z=p.z),  # type: ignore
            Orientation=ua.ThreeDOrientation(A=o.a, B=o.b, C=o.c),  # type: ignore
        )

    @classmethod
    def from_ua(cls, frame: ua.ThreeDFrame, *, length_unit: Unit, angle_unit: Unit) -> Pose:
        """Convert from ``ThreeDFrame`` whose numbers are in the given units."""
        p, o = frame.CartesianCoordinates, frame.Orientation
        return cls(
            position=Position(float(p.X), float(p.Y), float(p.Z), length_unit),
            orientation=Orientation(float(o.A), float(o.B), float(o.C), angle_unit),
        )


class CartesianFrame(UaObject):
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
        initial_pose: Pose,
        initial_base: CartesianFrame | ua.NodeId | None,
        minimum_access_level: int | None = None,
        bypass_lock: bool = False,
        **kwargs,
    ) -> None:
        """Initialize a Cartesian frame with position and orientation.

        :param name: Name of the frame.
        :param initial_pose: 3D Pose of the frame.
        :param initial_base: Optional frame or a node ID of the base frame relative to which this frame is specified.
        :param minimum_access_level: Minimum write access level of the frame variable. Read access is always allowed.
        :param bypass_lock: Whether the frame variable can be written to without ownership of the corresponding lock.
        """
        super().__init__(name=name, minimum_access_level=minimum_access_level, bypass_lock=bypass_lock, **kwargs)
        self._name = name
        self._initial_pose: Pose = initial_pose
        self._length_unit: Unit = self._initial_pose.position.unit
        self._angle_unit: Unit = self._initial_pose.orientation.unit
        self._initial_base: CartesianFrame | ua.NodeId | None = initial_base
        self._frame_lock = asyncio.Lock()
        self._datachange_callback_handle: int = 0  # 0 is an invalid asyncua handle

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
        await self._ua_position.set_writable(False)  # OPC UA spec allows it, but we don't
        self._ua_position_x = await self._ua_position.get_child("X")
        self._ua_position_y = await self._ua_position.get_child("Y")
        self._ua_position_z = await self._ua_position.get_child("Z")

        self._ua_orientation = await self.ua_node.get_child(f"{ns}:Orientation")
        await self._ua_orientation.set_writable(False)  # OPC UA spec allows it, but we don't
        self._ua_orientation_a = await self._ua_orientation.get_child(f"{ns}:A")
        self._ua_orientation_b = await self._ua_orientation.get_child(f"{ns}:B")
        self._ua_orientation_c = await self._ua_orientation.get_child(f"{ns}:C")

        await self.write_base(self._initial_base)
        await write_unit(self._ua_position, self._length_unit, engineering_unit="LengthUnit")
        await write_unit(self._ua_orientation, self._angle_unit, engineering_unit="AngleUnit")

        await self.write_pose(self._initial_pose)
        await self._sync_children(await self.ua_node.read_value())
        await self.ua_node.set_writable(True)  # as per spec

        status_code, self._datachange_callback_handle = self.server.ua_server.iserver.aspace.add_datachange_callback(
            self.ua_node.nodeid, ua.attribute_ids.AttributeIds.Value, callback=self.on_value_change
        )
        assert status_code.is_good()

        yield
        # shutdown
        self.server.ua_server.iserver.aspace.delete_datachange_callback(self._datachange_callback_handle)
        self._datachange_callback_handle = 0

    async def write_base(self, base: CartesianFrame | ua.NodeId | None) -> None:
        """Write the base coordinate frame reference to the OPC UA server."""
        if base is None:
            with contextlib.suppress(BadTypeMismatch):  # WorldFrame base is Null variant, so we don't need to write
                await write_value(self._ua_base, None)
        else:
            node_id = base.ua_node.nodeid if isinstance(base, CartesianFrame) else base
            await write_value(self._ua_base, node_id, variant_type=ua.VariantType.NodeId)

    async def read_base(self) -> CartesianFrame | None:
        """Read the base coordinate frame reference from the OPC UA server."""
        node_id: ua.NodeId | None = await read_value(self._ua_base)
        if node_id is None:
            return None
        obj = self.server.get_ua_object(node_id)
        assert isinstance(obj, CartesianFrame), f"base {obj.__class__.__name__} is no instance of CartesianFrame!"
        return obj

    async def write_pose(self, pose: Pose) -> None:
        """Replace the pose of this frame."""
        async with self._frame_lock:
            await self._write_pose_locked(pose)
        self.logger.debug("Written pose", pose=pose)

    async def _write_pose_locked(self, pose: Pose) -> None:
        await self.ua_node.write_value(pose.to_ua(length_unit=self._length_unit, angle_unit=self._angle_unit))

    async def read_pose(self, *, length_unit: Unit | None = None, angle_unit: Unit | None = None) -> Pose:
        """Read the current pose of this frame relative to its base."""
        pose = await self._read_pose_locked()
        if length_unit is not None:
            pose = pose.with_position(pose.position.to(length_unit))
        if angle_unit is not None:
            pose = pose.with_orientation(pose.orientation.to(angle_unit))
        return pose

    async def _read_pose_locked(self) -> Pose:
        return Pose.from_ua(await self.ua_node.read_value(), length_unit=self._length_unit, angle_unit=self._angle_unit)

    async def update_pose(self, *, position: Position | None = None, orientation: Orientation | None = None) -> Pose:
        """Atomically replace position and/or orientation, keeping the rest. Returns the new pose."""
        async with self._frame_lock:
            pose = await self._read_pose_locked()
            if position is not None:
                pose = pose.with_position(position)
            if orientation is not None:
                pose = pose.with_orientation(orientation)
            await self._write_pose_locked(pose)
        return pose

    async def write_position(self, position: Position) -> None:
        """Write the given Cartesian position to the OPC UA server. Keeps the current orientation of the frame."""
        await self.update_pose(position=position)

    async def read_position(self) -> Position:
        """Read the Cartesian position from the OPC UA server."""
        return (await self.read_pose()).position

    async def write_orientation(self, orientation: Orientation) -> None:
        """Write given angular orientation to the OPC UA server. Keeps the current position of the frame."""
        await self.update_pose(orientation=orientation)

    async def read_orientation(self) -> Orientation:
        """Read the angular orientation from the OPC UA server."""
        return (await self.read_pose()).orientation

    async def _sync_children(self, frame: ua.ThreeDFrame) -> None:
        try:
            position: ua.ThreeDCartesianCoordinates = frame.CartesianCoordinates
            await self._ua_position.write_value(position)
            await self._ua_position_x.write_value(ua.Double(position.X))
            await self._ua_position_y.write_value(ua.Double(position.Y))
            await self._ua_position_z.write_value(ua.Double(position.Z))

            orientation: ua.ThreeDOrientation = frame.Orientation
            await self._ua_orientation.write_value(orientation)
            await self._ua_orientation_a.write_value(ua.Double(orientation.A))
            await self._ua_orientation_b.write_value(ua.Double(orientation.B))
            await self._ua_orientation_c.write_value(ua.Double(orientation.C))
            self.logger.debug("Synced frame children", frame=frame)
        except Exception:
            self.logger.exception("Failed to sync frame children", frame=frame)

    async def on_value_change(self, _handle: int, dv: ua.DataValue) -> None:
        """Handle OPC UA data change notification to keep children in sync."""
        if dv is not None and (variant := dv.Value) is not None and isinstance(value := variant.Value, ua.ThreeDFrame):
            await self._sync_children(value)
        else:
            self.logger.warning("Received invalid data change notification", ua_data_value=dv, path=self.path)


class PositionFrame(CartesianFrame):
    """Cartesian position frame with the fixed OPC UA name ``PositionFrame``.

    A position frame defines a Cartesian frame relative to a required base coordinate frame.
    """

    def __init__(
        self,
        *,
        initial_pose: Pose,
        initial_base: CartesianFrame | ua.NodeId,
        minimum_access_level: int | None = None,
        bypass_lock: bool = False,
        **kwargs,
    ) -> None:
        """*Cooperative* constructor."""
        super().__init__(
            name="PositionFrame",
            initial_pose=initial_pose,
            initial_base=initial_base,
            minimum_access_level=minimum_access_level,
            bypass_lock=bypass_lock,
            **kwargs,
        )


class WorldFrame(CartesianFrame):
    """Root Cartesian frame with the fixed OPC UA name ``WorldFrame``.

    The world frame defines a Cartesian frame *without* a base coordinate frame.
    """

    def __init__(
        self,
        *,
        initial_pose: Pose,
        minimum_access_level: int | None = None,
        bypass_lock: bool = False,
        **kwargs,
    ) -> None:
        """*Cooperative* constructor."""
        super().__init__(
            name="WorldFrame",
            initial_pose=initial_pose,
            initial_base=None,
            minimum_access_level=minimum_access_level,
            bypass_lock=bypass_lock,
            **kwargs,
        )


class SpatialObject(UaObject):
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
        assert not attach_point.is_initialized, "Given attach_point must not be be initialized!"
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


class SpatialObjectList(ParentMixin[UaObject], UaObject):
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
