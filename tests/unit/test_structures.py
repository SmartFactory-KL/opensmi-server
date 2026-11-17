# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from dataclasses import dataclass, field

import pytest
from asyncua import ua
from asyncua.ua.status_codes import StatusCodes

from opensmi.server import UaVariable


@dataclass
class Point:
    x: ua.Double = 0.0
    y: ua.Double = 0.0


@dataclass
class Shape:
    corners: list[Point] = field(default_factory=list)


def array() -> UaVariable:
    return UaVariable(initial_value=[Point()])


def scalar() -> UaVariable:
    return UaVariable(initial_value=Point())


@pytest.mark.asyncio
async def test_an_array_variable_accepts_arrays_of_the_structure() -> None:
    await array().write_check([])
    await array().write_check([Point(1.0, 2.0), Point()])
    await array().write_check((Point(),))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "value",
    [1.0, "text", Point(), [1.0], [Shape()], [Point(), Shape()]],
    ids=["float", "string", "scalar structure", "array of float", "other structure", "mixed"],
)
async def test_an_array_variable_refuses_anything_else_with_bad_type_mismatch(value: object) -> None:
    with pytest.raises(ua.UaStatusCodeError) as refused:
        await array().write_check(value)  # pyright: ignore[reportArgumentType]
    assert refused.value.code == StatusCodes.BadTypeMismatch


@pytest.mark.asyncio
@pytest.mark.parametrize("value", [1.0, Shape(), [Point()]], ids=["float", "other structure", "array"])
async def test_a_scalar_variable_refuses_anything_but_its_class(value: object) -> None:
    await scalar().write_check(Point(1.0, 2.0))
    with pytest.raises(ua.UaStatusCodeError) as refused:
        await scalar().write_check(value)  # pyright: ignore[reportArgumentType]
    assert refused.value.code == StatusCodes.BadTypeMismatch


@pytest.mark.asyncio
async def test_a_write_check_callback_does_not_replace_the_structure_check() -> None:
    seen: list[object] = []
    variable = UaVariable(initial_value=Point(), write_check_callback=seen.append)

    with pytest.raises(ua.UaStatusCodeError):
        await variable.write_check(Shape())  # pyright: ignore[reportArgumentType]
    await variable.write_check(Point(3.0, 4.0))

    assert seen == [Point(3.0, 4.0)]


@pytest.mark.asyncio
async def test_none_passes_the_structure_check_of_an_optional_variable() -> None:
    await UaVariable(initial_value=Point(), optional_ok=True).write_check(None)
