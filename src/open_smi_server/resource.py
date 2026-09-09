# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Everything related to resources."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from dataclasses import dataclass
from typing import TYPE_CHECKING

from asyncua import ua
from typing_extensions import override

from open_smi_common.lifecycle_mixin import lifecycle
from open_smi_common.nodesets import SmartFactoryMachineSetNodeIds
from open_smi_server.mixins.parent_mixin import ParentMixin
from open_smi_server.mixins.ua_variable_container_mixins import AttributesMixin, IdentificationMixin
from open_smi_server.ua_object import UaObject, UaObjectDefinition
from open_smi_server.ua_variable import UaVariable
from open_smi_server.ua_variable_containers import Attributes, Identification

if TYPE_CHECKING:
    from open_smi_server.ua_object_containers import Resources as Resources


@dataclass(frozen=True, kw_only=True, slots=True)
class ResourceDefinition:
    """Definition of a `Resource`. Will be used to set OPC UA variables of a resource."""

    asset_id: str
    component_name: str
    resource_class: str
    form: str = ""
    color: str = ""


class ResourceAttributes(Attributes):
    """Base attributes for `Resource`."""

    Color = UaVariable(initial_value="")
    Form = UaVariable(initial_value="")


class ResourceIdentification(Identification):
    """Identification for `Resource`."""

    AssetId: UaVariable[str]
    """Asset ID of this resource."""
    ComponentName: UaVariable[ua.LocalizedText]
    """Human-readable (component) name of this resource."""
    ResourceClass: UaVariable[str]
    """Type ('ResourceClass') of this resource, i.e. 'Cab', 'Trailer' etc."""

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=SmartFactoryMachineSetNodeIds.ResourceItemIdentificationType,
            namespace_uri=SmartFactoryMachineSetNodeIds,
        )


class Resource(
    UaObject,
    IdentificationMixin[ResourceIdentification],
    ParentMixin["Resources"],
    AttributesMixin[ResourceAttributes],
):
    """Base class for all resources."""

    def __init__(self, *, definition: ResourceDefinition) -> None:
        """*Cooperative* constructor."""
        assert definition is not None
        assert len(definition.component_name) > 1, f"component_name {definition.component_name} is too short"
        assert len(definition.resource_class) > 1, f"resource_class {definition.resource_class} is too short"
        super().__init__(name=definition.component_name)
        self._resource_class: str = definition.resource_class
        self._definition = definition

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=SmartFactoryMachineSetNodeIds.ResourceType)

    @override
    async def _write_identification(self) -> None:
        await self.identification.AssetId.write(self._definition.asset_id)
        await self.identification.ComponentName.write(ua.LocalizedText(self._definition.component_name, "en-US"))
        await self.identification.ResourceClass.write(self._definition.resource_class)

    @lifecycle(after=AttributesMixin.lifecycle_attributes)
    async def lifecycle_resource(self) -> AsyncGenerator[None]:
        """Initialize the OPC UA variables based on the definition."""
        await self.attributes.Color.write(self._definition.color)
        await self.attributes.Form.write(self._definition.form)

        del self._definition  # no longer required
        yield
        # no shutdown

    @property
    def resource_class(self) -> str:
        """Return the class of the resource, i.e. 'Cab', 'Trailer' etc."""
        return self._resource_class
