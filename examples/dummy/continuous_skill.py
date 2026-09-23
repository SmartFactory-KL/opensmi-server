# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

from __future__ import annotations

import asyncio
import random
import time

from typing_extensions import override

from opensmi.server import (
    BaseMachineryItem,
    BaseSkillContinuous,
    BaseSkillFinalResultData,
    Monitoring,
    ParameterSet,
    UaVariable,
)
from opensmi.server.mixins import FinalResultDataMixin, MonitoringMixin, ParameterSetMixin, ParentMixin


class ContinuousSkillParameterSet(ParameterSet):
    delay = UaVariable(initial_value=0.0, historize=True, unit="s", range=(0.1, 10))


class ContinuousSkillMonitoring(Monitoring):
    rng = UaVariable(
        initial_value=15.0,  # must be inside the range
        historize=True,
        unit=4408652,  # unit id for "°C"
        range=(15, 35),
    )
    rng_int = UaVariable(initial_value=15)
    runtime = UaVariable(initial_value=0.0, historize=True, unit="s")


class ContinuousSkill(
    ParentMixin[BaseMachineryItem],
    ParameterSetMixin[ContinuousSkillParameterSet],
    MonitoringMixin[ContinuousSkillMonitoring],
    FinalResultDataMixin[BaseSkillFinalResultData],
    BaseSkillContinuous,
):
    """A demonstration of a continuous skill."""

    def __init__(self, name: str, delay: float = 1.0):
        super().__init__(name=name)

        self.parameter_set.delay.initial_value = delay

    @override
    async def _handle_resetting(self):
        # define logic that is executed after entering the RESETTING state

        await asyncio.sleep(await self.parameter_set.delay.read())  # simulate longer resetting phase

        await self.monitoring.reset_all()

    @override
    async def _handle_halting(self):
        # define logic that is executed after entering the HALTING state
        await asyncio.sleep(await self.parameter_set.delay.read())  # simulate longer halting phase

    @override
    async def _handle_starting(self):
        # define logic that is executed after entering the STARTING state
        await asyncio.sleep(await self.parameter_set.delay.read())  # simulate longer startup phase

    @override
    async def _handle_running(self):
        # define logic that is executed after entering the RUNNING state
        time_start = time.monotonic()

        # can (and should) block indefinitely, because by default this code is
        # canceled after halt() of the skill is called
        while True:
            await self.monitoring.rng.write(random.uniform(15, 35))
            await self.monitoring.rng_int.write(int(random.uniform(15, 35)))
            await self.monitoring.runtime.write(time.monotonic() - time_start)

            # parameters can also be read while the skill is running...
            delay = await self.parameter_set.delay.read()
            await asyncio.sleep(delay)
