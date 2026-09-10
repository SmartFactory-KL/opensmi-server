# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixins related to OPC UA spatial objects."""

from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

from asyncua import ua
from opensmi.core.errors import OpenSmiRuntimeError
from opensmi.core.lifecycle_mixin import lifecycle

from opensmi.server.mixins.ua_variable_container_mixins import MonitoringMixin
from opensmi.server.nodesets import RSLNodeIds
from opensmi.server.spatial_object import Orientation, Position, SpatialObject, SpatialObjectList, WorldFrame
from opensmi.server.ua_object import UaObject

if TYPE_CHECKING:
    from opensmi.server.base_machine import BaseMachine


class SpatialObjectMixin:
    """Mixin for `UaObject` that have a OPC UA spatial object."""

    __spatial_object: SpatialObject | None = None

    async def _init_spatial_object(self, spatial_object: SpatialObject) -> None:
        assert spatial_object is not None, "No spatial object given!"

        if not spatial_object.is_initialized:
            spatial_object.parent = self  # pyright: ignore[reportAttributeAccessIssue]
            await spatial_object.ua_create_node(self.monitoring.ua_node)  # pyright: ignore[reportAttributeAccessIssue]
            await spatial_object.init()

        root_parent = getattr(self, "root_parent", None)
        assert isinstance(root_parent, BaseMachine)
        spatial_object_list = getattr(root_parent, "spatial_object_list", None)
        if not isinstance(spatial_object_list, SpatialObjectList):
            msg = f"Spatial object list of {root_parent.name} was not initialized!"
            raise OpenSmiRuntimeError(msg)
        await spatial_object_list.add_spatial_object(spatial_object)
        self.__spatial_object = spatial_object

    @property
    def spatial_object(self) -> SpatialObject:
        """The spatial object that belongs to this object. Read-only property."""
        assert self.__spatial_object is not None, "Spatial object was not initialized!"
        return self.__spatial_object


class SpatialObjectListMixin:
    """Mixin for `UaObject` that have a OPC UA spatial object list."""

    __spatial_object_list: SpatialObjectList | None = None

    @lifecycle(after=MonitoringMixin.lifecycle_monitoring, requires=MonitoringMixin.lifecycle_monitoring)
    async def lifecycle_spatial_object_list(self) -> AsyncGenerator[None]:
        """Initialize the `SpatialObjectList`."""
        assert isinstance(self, UaObject)
        self.__spatial_object_list = SpatialObjectList(
            world_frame=WorldFrame(
                position=Position(0, 0, 0),
                orientation=Orientation(0, 0, 0),
            ),
            parent=self,  # pyright: ignore[reportArgumentType]
        )
        await self.__spatial_object_list.ua_create_node(self.monitoring.ua_node)  # pyright: ignore[reportAttributeAccessIssue]
        await self.__spatial_object_list.init()

        ns = self.server.ua_get_namespace_index(RSLNodeIds.URI)
        ua_rsl_location = await self.server.ua_server.get_objects_node().get_child(  # pyright: ignore[reportAttributeAccessIssue]
            f"{ns}:RelativeSpatialLocations"
        )
        await ua_rsl_location.add_reference(
            target=self.__spatial_object_list.ua_node, reftype=ua.object_ids.ObjectIds.Organizes
        )

        yield
        # no shutdown

    @property
    def spatial_object_list(self) -> SpatialObjectList:
        """The spatial object list that belongs to this object. Read-only property."""
        if self.__spatial_object_list is None:
            msg = "Spatial object list was not initialized!"
            raise OpenSmiRuntimeError(msg)
        return self.__spatial_object_list
