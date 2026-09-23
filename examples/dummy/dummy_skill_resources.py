# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

from typing import TYPE_CHECKING

from asyncua import ua
from typing_extensions import override

from opensmi.server import BaseMachineryItem, BaseSkillFinite, FinalResultData, ParameterSet, Resource, UaVariable
from opensmi.server.mixins import FinalResultDataMixin, ParameterSetMixin, ParentMixin

if TYPE_CHECKING:
    from dummy_machine import DummyMachine  # prevent circular import


class _ParameterSet(ParameterSet):
    TestResource = UaVariable(initial_value=None, variable_type=Resource)


class _FinalResultData(FinalResultData):
    ComponentName = UaVariable(initial_value=ua.LocalizedText())
    AssetId = UaVariable(initial_value="")
    ResourceClass = UaVariable(initial_value="")
    Color = UaVariable(initial_value="")
    Form = UaVariable(initial_value="")


class DummySkillResources(
    ParentMixin[BaseMachineryItem],
    ParameterSetMixin[_ParameterSet],
    FinalResultDataMixin[_FinalResultData],
    BaseSkillFinite,
):
    @override
    async def _init(self) -> None:
        await super()._init()
        machine: DummyMachine = self.root_parent  # pyright: ignore[reportAssignmentType]
        # setup references so clients know which node IDs are accepted
        for resource in machine.resources:
            await self.parameter_set.TestResource.add_reference(resource)
        self.parameter_set.TestResource.initial_value = machine.resources["Cab_A_Blue"]

    @override
    async def _handle_resetting(self) -> None:
        await self.final_result_data.reset_all()

    @override
    async def _handle_running(self):
        resource = await self.parameter_set.TestResource.read()

        await self.final_result_data.write_all(await resource.attributes.read_all())
        await self.final_result_data.write_all(await resource.identification.read_all())

        # or do it manually
        # await self.final_result_data.ComponentName.write(await resource.identification.ComponentName.read())
        # await self.final_result_data.AssetId.write(await resource.identification.AssetId.read())
        # await self.final_result_data.ResourceClass.write(await resource.identification.ResourceClass.read())
        # await self.final_result_data.Color.write(await resource.attributes.Color.read())
        # await self.final_result_data.Form.write(await resource.attributes.Form.read())
