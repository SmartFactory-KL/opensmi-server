# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio
import math
import random
from typing import Never

from asyncua import ua
from dummy_method import DummyMethod
from dummy_skill_simple import DummySkillSimple
from typing_extensions import override

from opensmi.server import BaseComponent, BaseMachine, BaseMachineryItem, Monitoring, ParameterSet, UaVariable
from opensmi.server.mixins import (
    MethodSetMixin,
    MonitoringMixin,
    NotificationForwarderMixin,
    ParameterSetMixin,
    ParentMixin,
    SkillSetMixin,
)
from opensmi.server.ua_object_containers import MethodSet, SkillSet


class DummyComponentParameterSet(ParameterSet):
    DummyStringParameter = UaVariable(initial_value="dummy")
    # bypass_lock=True allows write-access without occupation
    DummyIntParameter = UaVariable(initial_value=42, unit="m", range=(0, 100), bypass_lock=True)
    DummyFloatParameter = UaVariable(initial_value=math.pi)
    DummyIntListParameter = UaVariable(initial_value=[42, 84])


class DummyComponentMonitoring(Monitoring):
    RandomValue = UaVariable(initial_value=0.0, unit="mm", range=(0, 1), historize=True)
    RandomList = UaVariable(initial_value=[0.0, 1.2])


class _SkillSet(SkillSet):
    DummySkillInComponent: DummySkillSimple


class _MethodSet(MethodSet):
    DummyMethod: DummyMethod


class DummyComponent(
    MonitoringMixin[DummyComponentMonitoring],
    ParameterSetMixin[DummyComponentParameterSet],
    ParentMixin[BaseMachineryItem],
    SkillSetMixin[_SkillSet],
    MethodSetMixin[_MethodSet],
    NotificationForwarderMixin,  # forward OPC UA logging to our parent
    BaseComponent,  # base class last
):
    def __init__(self, *, name: str, param: int):
        super().__init__(name=name)

        # param is a custom parameter, just for demonstration
        self.param = param

    @override
    async def _write_identification(self) -> None:
        # These identification variables must be set for components
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )

    async def _init(self) -> None:
        await super()._init()

        await self.add_skill(DummySkillSimple(name="DummySkillInComponent"))
        await self.add_method(DummyMethod(name="DummyMethod"))

        if isinstance(self.parent, BaseMachine):  # prevent infinite
            await self.add_component(DummyComponent(name="NestedDummyComponent", param=self.param + 1))

        self._monitoring_update_task = asyncio.ensure_future(self._update_monitoring())

    @override
    async def _shutdown(self) -> None:
        await super()._shutdown()

        self._monitoring_update_task.cancel()

    async def _update_monitoring(self) -> Never:
        while True:
            await self.monitoring.RandomValue.write(random.random())

            new_list = [random.random() for _ in range(random.randrange(2, 5))]
            await self.monitoring.RandomList.write(new_list)

            await asyncio.sleep(1)
