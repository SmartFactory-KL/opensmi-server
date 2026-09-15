# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio

from typing_extensions import override

from opensmi.server import BaseMachineryItem, BaseSkillFinite, Monitoring, ParameterSet, UaVariable
from opensmi.server.mixins import MonitoringMixin, ParameterSetMixin, ParentMixin


class _ParameterSet(ParameterSet):
    MaxSteps = UaVariable(100, historize=True)
    TimePerStep = UaVariable(1.0, historize=True, unit="s", range=(0, 1))


class _Monitoring(Monitoring):
    Progress = UaVariable(0.0, unit="pct")


class SuspendableDummySkill(
    ParentMixin[BaseMachineryItem],
    ParameterSetMixin[_ParameterSet],
    MonitoringMixin[_Monitoring],
    BaseSkillFinite,
):
    """Dummy skill demonstrating how suspendable skills work."""

    def __init__(self):
        super().__init__(name="SuspendableDummySkill", suspendable=True)

    @override
    async def _handle_resetting(self):
        # (Optional) define logic that is executed in the RESETTING state
        self.logger.info("handling suspend")

        # reset our variable(s)
        await self.monitoring.Progress.reset()  # reset individually
        await self.parameter_set.reset_all()  # or all

    @override
    async def _handle_suspending(self) -> None:
        # (Optional) define logic that is executed in the SUSPENDING state
        self.logger.info("handling suspend")

    @override
    async def _handle_starting(self) -> None:
        # (Optional) define logic that is executed in the STARTING state
        self.logger.info("handling starting")

    @override
    async def _handle_running(self):
        # define logic that is executed in the RUNNING state
        self.logger.info("handling running")

        # read our parameters. Note that they are changeable while the skill is running
        # read them once like this ...
        max_steps = await self.parameter_set.MaxSteps.read()

        step = 0
        while step < max_steps:
            await self._suspend_point()  # block task if suspending / suspended
            step += 1
            await self.monitoring.Progress.write(step / max_steps * 100)

            # simulate long-running calculation etc. (and time to suspend this skill...)
            await asyncio.sleep(await self.parameter_set.TimePerStep.read())  # ... or continuously in a loop

        await self.monitoring.Progress.write(100.0)
