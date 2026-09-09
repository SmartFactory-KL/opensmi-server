# SPDX-FileCopyrightText: 2026 The Authors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import time

from asyncua import ua
from typing_extensions import override

from open_smi_server import BaseMachine, BaseSkillContinuous, Monitoring, ParameterSet
from open_smi_server.mixins import MonitoringMixin, ParameterSetMixin, ParentMixin
from open_smi_server.ua_variable import UaVariable
from pyuaadapter_sfkl.components.base_storage import BaseStorage
from pyuaadapter_sfkl.skills.storage_skills import StoreAllEmptySkill, update_storage_random


class _ParameterSet(ParameterSet):
    delay = UaVariable(initial_value=1.0, historize=True, unit="s", range=(0.1, 10))


class _Monitoring(Monitoring):
    runtime = UaVariable(initial_value=0.0, historize=True, unit="s")


class StorageSimulationSkill(
    BaseSkillContinuous,
    ParentMixin[BaseStorage],
    ParameterSetMixin[_ParameterSet],
    MonitoringMixin[_Monitoring],
):
    def __init__(self):
        super().__init__(name="StorageSimulation")

    @override
    async def _handle_resetting(self):
        # reset our result variable(s)
        await self.monitoring.runtime.reset()

    @override
    async def _handle_running(self):
        # code during STARTING state
        time_start = time.monotonic()

        while True:
            delay = await self.parameter_set.delay.read()

            await update_storage_random(self.parent, chance_free=0.25)

            await self.monitoring.runtime.write(time.monotonic() - time_start)

            await asyncio.sleep(delay)


class DummyStorage(BaseStorage, ParentMixin[BaseMachine]):
    @override
    async def _init(self) -> None:
        await super()._init()

        await self.identification.SerialNumber.write("123456789")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )

        await self.add_skill(StoreAllEmptySkill())
        await self.add_skill(StorageSimulationSkill())
