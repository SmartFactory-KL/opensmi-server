# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from dataclasses import dataclass, field

import pytest
from asyncua import ua
from asyncua.ua.status_codes import StatusCodes

from opensmi.server import UaVariable
from opensmi.server.structures import structure_array_write_check


@dataclass
class Point:
    x: ua.Double = 0.0
    y: ua.Double = 0.0


@dataclass
class Shape:
    corners: list[Point] = field(default_factory=list)


def variable() -> UaVariable:
    return UaVariable(initial_value=[Point()], write_check_callback=structure_array_write_check(Point))


@pytest.mark.asyncio
async def test_write_check_accepts_arrays_of_the_structure() -> None:
    await variable().write_check([])
    await variable().write_check([Point(1.0, 2.0), Point()])
    await variable().write_check((Point(),))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [1.0, "text", Point(), [1.0], [Shape()], [Point(), Shape()]],
    ids=["float", "string", "scalar structure", "array of float", "other structure", "mixed"],
)
async def test_write_check_refuses_anything_else_with_bad_type_mismatch(value: object) -> None:
    with pytest.raises(ua.UaStatusCodeError) as refused:
        await variable().write_check(value)  # pyright: ignore[reportArgumentType]
    assert refused.value.code == StatusCodes.BadTypeMismatch
