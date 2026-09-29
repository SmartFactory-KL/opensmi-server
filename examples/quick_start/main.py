# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

import asyncio

from asyncua import ua
from opensmi.core import Unit

from opensmi.server import BaseMachine, BaseSkillFinalResultData, BaseSkillFinite, ParameterSet, Server, UaVariable
from opensmi.server.mixins import FinalResultDataMixin, ParameterSetMixin, ParentMixin


class ExampleSkillParameterSet(ParameterSet):
    """Parameters of the example skill."""

    x = UaVariable(0, unit=Unit.NANOAMPERE, range=(0, 10))
    """First parameter of the computation."""
    y = UaVariable(0, unit="nA", range=(0, 10))
    """Second parameter of the computation."""


class ExampleSkillSimpleFinalResultData(BaseSkillFinalResultData):
    """Final result data of the example skill."""

    ComputationResult = UaVariable(0, unit="nA", range=(0, 20))
    """Result of the computation."""


class ExampleSkill(
    ParameterSetMixin[ExampleSkillParameterSet],  # provides type-checkable parameters
    FinalResultDataMixin[ExampleSkillSimpleFinalResultData],  # provides type-checkable results
    BaseSkillFinite,  # provides finite skill logic etc.
):
    """Compute the sum of ``x`` and ``y`` parameters and store the result in ``ComputationResult``."""

    async def _handle_running(self) -> None:
        # define logic that is executed in the RUNNING state

        # read our parameters
        x = await self.parameter_set.x.read()
        y = await self.parameter_set.y.read()

        # simulate long-running calculation etc.
        await asyncio.sleep(1)

        # we are done, write return variables
        await self.final_result_data.ComputationResult.write(x + y)


class ExampleMachine(BaseMachine):
    """Quick start example machine with a single dummy skill."""

    async def _init(self) -> None:
        await super()._init()

        await self.add_skill(ExampleSkill())

    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )


async def main() -> None:
    async with Server() as server:  # will properly shut down the server
        await server.add_machine(ExampleMachine())  # add machine(s)
        await server.start(blocking=True)  # start the server and block while it is running


if __name__ == "__main__":
    asyncio.run(main())
