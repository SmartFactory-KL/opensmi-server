# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio

from opensmi.core import SkillState
from typing_extensions import override

from opensmi.server import BaseMachineryItem, BaseSkillFinite, Monitoring, UaVariable
from opensmi.server.mixins import FinalResultDataMixin, MonitoringMixin, ParameterSetMixin, ParentMixin

from .dummy_skill_simple import DummySkillSimpleFinalResultData, DummySkillSimpleParameterSet

MAX_STEPS = 100


class SuspendableDummySkillMonitoring(Monitoring):
    Progress = UaVariable(initial_value=0.0, unit="pct")


class SuspendableDummySkill(
    BaseSkillFinite,
    ParentMixin[BaseMachineryItem],
    ParameterSetMixin[DummySkillSimpleParameterSet],
    FinalResultDataMixin[DummySkillSimpleFinalResultData],
    MonitoringMixin[SuspendableDummySkillMonitoring],
):
    def __init__(self):
        super().__init__(name="SuspendableDummySkill", suspendable=True)

    @override
    async def _init(self) -> None:
        await super()._init()

    @override
    async def _handle_resetting(self):
        # reset our result variable(s)
        await self.monitoring.Progress.reset()  # reset individually
        await self.final_result_data.reset_all()  # or all

    @override
    async def _handle_running(self):
        # define logic that is executed in the RUNNING state

        # read our parameters. Note that they are changeable while the skill is running
        x = await self.parameter_set.x.read()
        y = await self.parameter_set.y.read()

        # simulate long-running calculation etc. (and time to suspend this skill...)
        step = 0
        while step < MAX_STEPS:
            if self.current_state == SkillState.RUNNING:
                # Only progress when RUNNING, not when SUSPENDED
                step += 1
                await self.monitoring.Progress.write(step / MAX_STEPS * 100)
            await asyncio.sleep(0.1)

        await self.monitoring.Progress.write(100.0)
        await self.final_result_data.ComputationResult.write(x + y)  # we are done, write return variables
