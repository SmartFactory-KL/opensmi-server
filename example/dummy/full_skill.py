# SPDX-FileCopyrightText: 2026 The Authors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import random

from asyncua import Node
from typing_extensions import override

from open_smi_common import OpenSmiRuntimeError
from open_smi_server import BaseSkillFinite
from open_smi_server import BaseFeasibilityCheck

# this would probably be a database or similar for real applications
feasibility_check_map = {}


class FullSkillFeasibility(BaseFeasibilityCheck):
    _param_x: Node
    _param_y: Node
    _ua_return_variable: Node

    def __init__(self):
        super().__init__(name="FullSkillFeasibility")

    @override
    async def _init(self) -> None:
        await super()._init()

        # add some parameter variables
        self._param_x = await self.add_parameter_variable("x", 0)
        self._param_y = await self.add_parameter_variable("y", 0)

        # add some result variables
        self._ua_return_variable = await self.add_result_variable("Feasibility_ID", 0)

    @override
    async def _handle_resetting(self):
        # define logic that is executed after entering the RESETTING state

        # reset our result variable(s)
        await self._ua_return_variable.write_value(0)

    @override
    async def _handle_running(self):
        # define logic that is executed in the RUNNING state

        # read our parameters. Note that they are changeable while the skill is running
        x, y = await asyncio.gather(self._param_x.read_value(), self._param_y.read_value())

        id_ = random.randint(0, 42)

        feasibility_check_map[id_] = (x, y)

        await self._ua_return_variable.write_value(id_)


class FullSkill(BaseSkillFinite):
    _ua_feasibility_id: Node
    _ua_return_variable: Node

    def __init__(self, *, name: str, delay: float = 1.0):
        super().__init__(name=name, feasibility_check=FullSkillFeasibility())
        self.delay = delay

    @override
    async def _init(self) -> None:
        await super()._init()

        self._ua_feasibility_id = await self.add_parameter_variable("Feasibility_ID", 0)

        # add some result variables
        self._ua_return_variable = await self.add_result_variable("Addition", 0)

    @override
    async def _handle_resetting(self):
        # define logic that is executed after entering the RESETTING state

        # reset our result variable(s)
        await self._ua_return_variable.write_value(0)

    @override
    async def _handle_running(self):
        # define logic that is executed in the RUNNING state

        _id = await self._ua_feasibility_id.read_value()
        try:
            # read our parameters from the previous feasibility check
            x, y = feasibility_check_map[_id]

            await asyncio.sleep(self.delay)  # simulate long running calculation etc.

            await self._ua_return_variable.write_value(x + y)  # we are done, write return variables
        except IndexError:
            raise OpenSmiRuntimeError(f"Invalid feasibility ID '{_id}' given!", "E9434") from None
