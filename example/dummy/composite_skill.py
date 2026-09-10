# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

from typing import TYPE_CHECKING

from opensmi.core import Unit
from typing_extensions import override

from opensmi.server import BaseSkillFinalResultData, BaseSkillFinite, Monitoring, UaVariable
from opensmi.server.mixins import FinalResultDataMixin, MonitoringMixin, ParentMixin, RequirementsMixin

if TYPE_CHECKING:
    from .dummy_machine import DummyMachine as DummyMachine


class CompositeDummySkillMonitoring(Monitoring):
    progress = UaVariable(initial_value=0.0, unit=Unit.PERCENT)


class CompositeDummySkillFinalResultData(BaseSkillFinalResultData):
    total = UaVariable(initial_value=0.0)


class CompositeDummySkill(
    BaseSkillFinite,
    RequirementsMixin,
    ParentMixin["DummyMachine"],
    MonitoringMixin[CompositeDummySkillMonitoring],
    FinalResultDataMixin[CompositeDummySkillFinalResultData],
):
    """Demonstration of a composite skill, meaning a skill, that is using at least one other skill internally."""

    def __init__(self):
        super().__init__(name="CompositeDummySkill")

    @override
    async def _init(self) -> None:
        await super()._init()

        self._other_skill = self.parent.skill_set.DummySkillWithoutGateRequirement

        # It's important to model dependencies in OPC UA for clients to understand relations!
        await self.add_dependency(self._other_skill)

    @override
    async def _handle_resetting(self):
        # define logic that is executed after entering the RESETTING state

        await self.monitoring.reset_all()
        await self.final_result_data.total.reset()

    @override
    async def _handle_halting(self):
        # define logic that is executed after entering the HALTING state

        await self._halt_running_called_skills()  # we need to halt every skill we called
        # Note: This halts every running skill called by us, if you want to exclude certain skills,
        # have to do it yourself!

    @override
    async def _handle_running(self):
        # define logic that is executed in the RUNNING state
        total = 0.0
        params = [(1, 2), (3, 4), (5, 6), (7, 8)]
        for index, (x, y) in enumerate(params):
            await self.monitoring.progress.write(float(index / len(params) * 100))
            await self._other_skill.parameter_set.x.write(x)
            await self._other_skill.parameter_set.y.write(y)
            async with self.call_other_finite_skill(self._other_skill):
                result = await self._other_skill.final_result_data.ComputationResult.read()
                self.logger.info("intermediate result", index=index, x=x, y=y, result=result)
                total += result

        await self.final_result_data.total.write(total)
        await self.monitoring.progress.write(100.0)
