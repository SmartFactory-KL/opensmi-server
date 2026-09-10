# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import math

from asyncua import ua
from typing_extensions import override

from opensmi.server import BaseMachine, Components, MethodSet, ParameterSet, SkillSet, UaVariable
from opensmi.server.mixins import (
    ComponentsMixin,
    MethodSetMixin,
    MonitoringMixin,
    ParameterSetMixin,
    SkillSetMixin,
)

# from pyuaadapter_sfkl.resources.carrier_resource import WST_A, WST_B, WST_C, WST_G
# from pyuaadapter_sfkl.resources.truck_resource import TRUCK_RESOURCES
from .composite_skill import CompositeDummySkill
from .continuous_skill import ContinuousSkill
from .continuous_skill_self_halting import ContinuousSkillSelfHalting
from .dummy_component import DummyComponent
from .dummy_method import DummyMethod
from .dummy_skill import DummySkill as DummySkillImpl
from .startup_skill import StartupSkill
from .suspendable_dummy_skill import SuspendableDummySkill


class DummyMachineParameterSet(ParameterSet):
    DummyStringParameter = UaVariable(initial_value="dummy")
    DummyIntParameter = UaVariable(initial_value=42, unit="cm", range=(0, 100))
    DummyFloatParameter = UaVariable(initial_value=math.pi)


class DummyMachineSkillSet(SkillSet):
    # DummySkill: DummySkillImpl
    DummySkillWithoutGateRequirement: DummySkillImpl
    # DummySkillResources: DummySkillResources
    ContinuousSkill: ContinuousSkill
    ContinuousSkillSelfHalting: ContinuousSkillSelfHalting
    CompositeDummySkill: CompositeDummySkill
    SuspendableDummySkill: SuspendableDummySkill
    StartupSkill: StartupSkill


class DummyMachineMethodSet(MethodSet):
    DummyMethod: DummyMethod


class DummyMachineComponents(Components):
    # Port_1: DummyPort  # TODO(CaHa): I don't like this, how is a normal user supposed to know how they are named?
    DummyComponent: DummyComponent
    # Port_2: DummyPort  # try this without actually providing it during _init() -> will fail loudly


class DummyMachine(
    MonitoringMixin,
    ParameterSetMixin[DummyMachineParameterSet],
    SkillSetMixin[DummyMachineSkillSet],
    MethodSetMixin[DummyMachineMethodSet],
    ComponentsMixin[DummyMachineComponents],
    # ResourcesMixin,
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

    # @override
    # async def _init_resources(self) -> None:
    #     # all truck resources, but there are also subsets available
    #     self.truck_resources: list[Resource] = [await self.resources.add(def_) for def_ in TRUCK_RESOURCES]
    #     self.carrier_resources: list[Resource] = [
    #         await self.resources.add(wst_def) for wst_def in [WST_A, WST_B, WST_C, WST_G]
    #     ]

    @override
    async def _init(self) -> None:
        await super()._init()

        # Components
        await self.add_component(DummyComponent(name="DummyComponent", param=42))
        # await self.add_component(DummyPort(port_number=1, rfid_tag_own=232, rfid_tag_neighbor=80, couple_delay=0.5))

        # Methods
        await self.add_method(DummyMethod(name="DummyMethod"))

        # Skills
        # await self.add_skill(DummySkillImpl(name="DummySkill", dependencies=[self.components.Port_1]))
        await self.add_skill(DummySkillImpl(name="DummySkillWithoutGateRequirement"))
        # await self.add_skill(DummySkillResources())
        await self.add_skill(ContinuousSkill(name="ContinuousSkill"))
        await self.add_skill(ContinuousSkillSelfHalting())
        await self.add_skill(CompositeDummySkill())
        # await self.add_skill(FullSkill(name="FullSkill"))  # TODO
        await self.add_skill(SuspendableDummySkill())
        await self.add_skill(StartupSkill())
        # Note: You could also add skill(s) to components if not possible directly in the component init code
