# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

from __future__ import annotations

import asyncio
import random
import time

from continuous_skill import ContinuousSkillMonitoring, ContinuousSkillParameterSet
from opensmi.core.errors import OpenSmiRuntimeError
from typing_extensions import override

from opensmi.server import BaseMachineryItem, BaseSkillContinuous, BaseSkillFinalResultData, UaVariable
from opensmi.server.mixins import FinalResultDataMixin, MonitoringMixin, ParameterSetMixin, ParentMixin


class ContinuousSkillSelfHaltingParameterSet(ContinuousSkillParameterSet):
    loops = UaVariable(initial_value=5)


class ContinuousSkillSelfHalting(
    ParentMixin[BaseMachineryItem],
    ParameterSetMixin[ContinuousSkillSelfHaltingParameterSet],
    MonitoringMixin[ContinuousSkillMonitoring],
    FinalResultDataMixin[BaseSkillFinalResultData],
    BaseSkillContinuous,
):
    """A demonstration of a continuous skill that halts after configurable delay from RUNNING state."""

    def __init__(self, *, delay: float = 1.0):
        super().__init__(name="ContinuousSkillSelfHalting")
        self.parameter_set.delay.initial_value = delay

    @override
    async def _handle_resetting(self):
        # define logic that is executed after entering the RESETTING state

        await self.monitoring.reset_all()

    @override
    async def _handle_halting(self):
        # define logic that is executed after entering the HALTING state
        pass  # We don't need to do anything for this dummy example

    @override
    async def _handle_starting(self):
        # define logic that is executed after entering the STARTING state
        await asyncio.sleep(await self.parameter_set.delay.read())  # simulate longer startup phase

    @override
    async def _handle_running(self):
        # define logic that is executed after entering the RUNNING state
        time_start = time.monotonic()
        for _ in range(await self.parameter_set.loops.read()):
            await self.monitoring.rng.write(random.uniform(15, 35))
            await self.monitoring.rng_int.write(int(random.uniform(15, 35)))
            await self.monitoring.runtime.write(time.monotonic() - time_start)

            # parameters can also be read while the skill is running...
            delay = await self.parameter_set.delay.read()
            await asyncio.sleep(delay)

        # simulate something going wrong
        raise OpenSmiRuntimeError("Simulated Error", error_code="E2130")
