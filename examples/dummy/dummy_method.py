# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

from typing_extensions import override

from opensmi.server import BaseMachineryItem, BaseMethod, FinalResultData, ParameterSet, UaVariable
from opensmi.server.mixins import FinalResultDataMixin, ParameterSetMixin, ParentMixin, RequirementsMixin


class DummyMethodParameterSet(ParameterSet):
    a = UaVariable(initial_value=0, historize=True)
    """First parameter of the addition."""
    b = UaVariable(initial_value=0, historize=True)
    """Second parameter of the addition."""


class DummyMethodFinalResultData(FinalResultData):
    Sum = UaVariable(initial_value=0, historize=True)
    """Result of the addition."""


class DummyMethod(
    ParentMixin[BaseMachineryItem],
    RequirementsMixin,
    FinalResultDataMixin[DummyMethodFinalResultData],
    ParameterSetMixin[DummyMethodParameterSet],
    BaseMethod,
):
    """Compute the sum of ``a`` and ``b`` parameters and store the result in ``Sum``."""

    @override
    async def execute_method(self) -> None:
        # read the parameters
        a = await self.parameter_set.a.read()
        b = await self.parameter_set.b.read()

        # calculate the sum
        result = a + b

        # write the result to the return variable
        await self.final_result_data.Sum.write(result)
