# SPDX-FileCopyrightText: 2026 The Authors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
from enum import IntEnum

from typing_extensions import override

from open_smi_server import BaseMachineryItem, BaseSkillFinite, UaVariable
from open_smi_server.mixins import FinalResultDataMixin, ParameterSetMixin, ParentMixin, RequirementsMixin
from .dummy_skill_simple import DummySkillSimpleFinalResultData, DummySkillSimpleParameterSet


# Enums are supported, too!
class OperationEnum(IntEnum):
    Addition = 0  # Important: These always start at 0 and increase by 1, the order is very important!
    Subtraction = 1
    Multiplication = 2


class DummySkillParameterSet(DummySkillSimpleParameterSet):
    TestBool = UaVariable(initial_value=False)
    TestString = UaVariable(initial_value="Hello World!")
    operation = UaVariable(initial_value=OperationEnum.Addition)


class DummySkill(
    BaseSkillFinite,
    ParentMixin[BaseMachineryItem],
    RequirementsMixin,
    ParameterSetMixin[DummySkillParameterSet],
    FinalResultDataMixin[DummySkillSimpleFinalResultData],
):
    def __init__(self, *, name: str, delay: float = 1, dependencies: list | None = None):
        super().__init__(name=name)
        self.delay = delay
        self.dependencies_to_add = dependencies if dependencies is not None else []

    @override
    async def _init(self) -> None:
        await super()._init()

        # add dependencies like skills or components (e.g. ports)
        for dependency in self.dependencies_to_add:
            await self.add_dependency(dependency)

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
        operation = await self.parameter_set.operation.read()

        await self.ua_log_info(f"Chosen operation mode is '{operation.name}'!")

        await asyncio.sleep(self.delay)  # simulate long-running calculation etc.

        if operation == OperationEnum.Addition:
            result = x + y
        elif operation == OperationEnum.Subtraction:
            result = x - y
        else:  # no other valid option
            result = x * y

        await self.final_result_data.ComputationResult.write(result)  # we are done, write return variables
