# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

"""Example demonstrating how a feasibility check can be used. Precondition checks work analogous."""

import asyncio
import random

from asyncua import ua
from opensmi.core.errors import OpenSmiRuntimeError
from typing_extensions import override

from opensmi.server import BaseMachine, BaseSkillFinalResultData, BaseSkillFinite, ParameterSet, Server, UaVariable
from opensmi.server.base_feasibility_check import BaseFeasibilityCheck
from opensmi.server.mixins import (
    FinalResultDataMixin,
    MonitoringMixin,
    ParameterSetMixin,
    SpatialObjectListMixin,
)

# this would probably be a database or similar for real applications
feasibility_check_map: dict[int, tuple[int, int]] = {}


class _FullSkillFeasibilityCheckParameterSet(ParameterSet):
    X = UaVariable(0)
    """First summand to check."""
    Y = UaVariable(0)
    """Second summand to check."""


class _FullSkillFeasibilityCheckFinalResultData(BaseSkillFinalResultData):
    FeasibilityID = UaVariable(0)
    """ID of the successful check, required to start ``FullSkill``."""


class FullSkillFeasibilityCheck(
    ParameterSetMixin[_FullSkillFeasibilityCheckParameterSet],
    FinalResultDataMixin[_FullSkillFeasibilityCheckFinalResultData],
    BaseFeasibilityCheck,
):
    """Check whether ``X`` + ``Y`` is odd and return a ``FeasibilityID`` for `FullSkill`."""

    @override
    async def _handle_resetting(self) -> None:
        await self.final_result_data.FeasibilityID.reset()

    @override
    async def _handle_running(self) -> None:
        x = await self.parameter_set.X.read()
        y = await self.parameter_set.Y.read()

        await asyncio.sleep(1)  # add your feasibility check here
        if (x + y) % 2 == 0:
            msg = f"{x=} + {y=} would result in an even number!"
            raise OpenSmiRuntimeError(msg, error_code="E9435")

        feasibility_id = random.randint(0, 42)

        feasibility_check_map[feasibility_id] = (x, y)

        await self.final_result_data.FeasibilityID.write(feasibility_id)


class _FullSkillParameterSet(ParameterSet):
    FeasibilityID = UaVariable(0, write_check_callback=lambda x: x in feasibility_check_map)
    """ID of a successful feasibility check, whose summands are added."""


class _FullSkillFinalResultData(BaseSkillFinalResultData):
    ComputationResult = UaVariable(0)
    """Sum of the summands of the feasibility check."""


class FullSkill(
    ParameterSetMixin[_FullSkillParameterSet],
    FinalResultDataMixin[_FullSkillFinalResultData],
    BaseSkillFinite,
):
    """Add the summands of the feasibility check with the given ``FeasibilityID``."""

    @override
    async def _handle_resetting(self) -> None:
        await self.final_result_data.ComputationResult.reset()

    @override
    async def _handle_running(self) -> None:
        feasibility_id = await self.parameter_set.FeasibilityID.read()

        # read our parameters from the previous feasibility check
        x, y = feasibility_check_map[feasibility_id]

        await asyncio.sleep(1)

        await self.final_result_data.ComputationResult.write(x + y)


class ExampleMachine(
    MonitoringMixin,
    SpatialObjectListMixin,
    BaseMachine,
):
    """Example machine with a skill using a feasibility check."""

    @override
    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )

    @override
    async def _init(self) -> None:
        await super()._init()

        skill = FullSkill()
        await self.add_skill(skill)
        await skill.add_feasibility_check(FullSkillFeasibilityCheck())


async def main() -> None:
    async with Server() as server:  # will properly shut down the server
        await server.add_machine(ExampleMachine())  # add machine(s)
        await server.start(blocking=True)  # start the server and block while it is running


if __name__ == "__main__":
    asyncio.run(main())
