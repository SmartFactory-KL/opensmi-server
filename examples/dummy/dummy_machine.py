# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import math

from asyncua import ua
from composite_skill import CompositeDummySkill
from continuous_skill import ContinuousSkill
from continuous_skill_self_halting import ContinuousSkillSelfHalting
from dummy_component import DummyComponent
from dummy_method import DummyMethod
from dummy_skill import DummySkill as DummySkillImpl
from dummy_skill_resources import DummySkillResources
from startup_skill import StartupSkill
from suspendable_dummy_skill import SuspendableDummySkill
from typing_extensions import override

from opensmi.server import BaseMachine, Components, MethodSet, ParameterSet, SkillSet, UaVariable
from opensmi.server.mixins import (
    ComponentsMixin,
    MethodSetMixin,
    MonitoringMixin,
    ParameterSetMixin,
    ResourcesMixin,
    SkillSetMixin,
)


class DummyMachineParameterSet(ParameterSet):
    DummyStringParameter = UaVariable(initial_value="dummy")
    DummyIntParameter = UaVariable(initial_value=42, unit="cm", range=(0, 100))
    DummyFloatParameter = UaVariable(initial_value=math.pi)


class DummyMachineSkillSet(SkillSet):
    DummySkill: DummySkillImpl
    DummySkillResources: DummySkillResources
    ContinuousSkill: ContinuousSkill
    ContinuousSkillSelfHalting: ContinuousSkillSelfHalting
    CompositeDummySkill: CompositeDummySkill
    SuspendableDummySkill: SuspendableDummySkill
    StartupSkill: StartupSkill


class DummyMachineMethodSet(MethodSet):
    DummyMethod: DummyMethod


class DummyMachineComponents(Components):
    DummyComponent: DummyComponent
    # DummyComponent2: DummyComponent  # try this without actually providing it during _init() -> will fail loudly


class DummyMachine(
    MonitoringMixin,
    ParameterSetMixin[DummyMachineParameterSet],
    SkillSetMixin[DummyMachineSkillSet],
    MethodSetMixin[DummyMachineMethodSet],
    ComponentsMixin[DummyMachineComponents],
    ResourcesMixin,
    BaseMachine,  # important! mixins etc. first
):
    @override
    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )

    @override
    async def _init_resources(self) -> None:
        from resources import BATTERY_PACK, CAB_A_BLUE

        await self.resources.add(BATTERY_PACK)
        await self.resources.add(CAB_A_BLUE)

    @override
    async def _init(self) -> None:
        await super()._init()

        # Components
        await self.add_component(DummyComponent(name="DummyComponent", param=42))

        # Methods
        await self.add_method(DummyMethod(name="DummyMethod"))

        # Skills
        await self.add_skill(DummySkillImpl(name="DummySkill", delay=0))
        await self.add_skill(DummySkillResources())
        await self.add_skill(ContinuousSkill(name="ContinuousSkill"))
        await self.add_skill(ContinuousSkillSelfHalting())
        await self.add_skill(CompositeDummySkill())
        await self.add_skill(SuspendableDummySkill())
        await self.add_skill(StartupSkill())
        # Note: You could also add skill(s) to components if not possible directly in the component init code
