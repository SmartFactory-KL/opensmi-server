# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

from typing_extensions import override

from opensmi.server import BaseMachineryItem, BaseMethod, FinalResultData, ParameterSet, UaObject, UaVariable
from opensmi.server.mixins import FinalResultDataMixin, ParameterSetMixin, ParentMixin, RequirementsMixin


class DummyMethodParameterSet(ParameterSet):
    a = UaVariable(initial_value=0, historize=True)
    b = UaVariable(initial_value=0, historize=True)


class DummyMethodFinalResultData(FinalResultData):
    Sum = UaVariable(initial_value=0, historize=True)


class DummyMethod(
    BaseMethod,
    ParentMixin[BaseMachineryItem],
    RequirementsMixin,
    FinalResultDataMixin[DummyMethodFinalResultData],
    ParameterSetMixin[DummyMethodParameterSet],
):
    def __init__(
        self,
        *,
        name: str | None = None,
        dependencies: list[UaObject] | None = None,
    ) -> None:
        super().__init__(name=name)
        self.dependencies_to_add = dependencies if dependencies is not None else []

    @override
    async def _init(self):
        await super()._init()

        # add dependencies like skills or components (e.g. ports)
        for dependency in self.dependencies_to_add:
            await self.add_dependency(dependency)

    async def execute_method(self) -> None:
        # read the parameters
        a = await self.parameter_set.a.read()
        b = await self.parameter_set.b.read()

        # calculate the sum
        result = a + b

        # write the result to the return variable
        await self.final_result_data.Sum.write(result)
