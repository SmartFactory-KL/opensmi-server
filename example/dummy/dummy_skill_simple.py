# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio

from typing_extensions import override

from opensmi.server import BaseMachineryItem, BaseSkillFinalResultData, BaseSkillFinite, ParameterSet, UaVariable
from opensmi.server.mixins import FinalResultDataMixin, ParameterSetMixin, ParentMixin


class DummySkillSimpleParameterSet(ParameterSet):
    x = UaVariable(initial_value=0, historize=True)
    y = UaVariable(initial_value=0, historize=True)


class DummySkillSimpleFinalResultData(BaseSkillFinalResultData):
    ComputationResult = UaVariable(initial_value=0, historize=True)


class DummySkillSimple(
    BaseSkillFinite,
    ParentMixin[BaseMachineryItem],
    ParameterSetMixin[DummySkillSimpleParameterSet],
    FinalResultDataMixin[DummySkillSimpleFinalResultData],
):
    @override
    async def _handle_resetting(self):
        # define logic that is executed after entering the RESETTING state
        await self.final_result_data.reset_all()

    @override
    async def _handle_halting(self):
        # define logic that is executed after entering the HALTING state

        pass  # We don't need to do anything for this dummy example

    @override
    async def _handle_running(self):
        # define logic that is executed in the RUNNING state

        # read our parameters. Note that they are changeable while the skill is running
        x = await self.parameter_set.x.read()
        y = await self.parameter_set.y.read()

        await asyncio.sleep(1)  # simulate long-running calculation etc.

        await self.final_result_data.ComputationResult.write(x + y)  # we are done, write return variables
