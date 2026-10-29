# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT
import math

import pytest
from opensmi.core import Unit

from opensmi.server.spatial_object import Orientation, Pose, Position


def test_position_converts_millimetres_to_metres() -> None:
    position = Position(1000, 250, -500, Unit.MILLIMETRE)

    result = position.to(Unit.METRE)

    assert result == Position(1.0, 0.25, -0.5, Unit.METRE)


def test_position_adds_values_in_left_operand_unit() -> None:
    metres = Position(1, 2, 3, Unit.METRE)
    millimetres = Position(500, 250, 1000, Unit.MILLIMETRE)

    result = metres + millimetres

    assert result == Position(1.5, 2.25, 4.0, Unit.METRE)


def test_position_init_rejects_unsupported_unit() -> None:
    with pytest.raises(ValueError, match="unsupported unit"):
        Position(unit=Unit.AMPERE)


def test_position_to_rejects_unsupported_unit() -> None:
    with pytest.raises(ValueError, match="unsupported unit"):
        Position().to(unit=Unit.AMPERE)


# ------------------------------------------------------------------------------------------


def test_orientation_converts_degrees_to_radians() -> None:
    orientation = Orientation(180, 90, -45, Unit.DEGREE_UNIT_OF_ANGLE)

    result = orientation.to(Unit.RADIAN)

    assert result.a == pytest.approx(math.pi)
    assert result.b == pytest.approx(math.pi / 2)
    assert result.c == pytest.approx(-math.pi / 4)
    assert result.unit == Unit.RADIAN


def test_orientation_init_rejects_unsupported_unit() -> None:
    with pytest.raises(ValueError, match="unsupported unit"):
        Orientation(unit=Unit.AMPERE)


def test_orientation_to_rejects_unsupported_unit() -> None:
    with pytest.raises(ValueError, match="unsupported unit"):
        Orientation().to(Unit.AMPERE)


# ------------------------------------------------------------------------------------------


def test_pose_ua_round_trip() -> None:
    pose = Pose(
        position=Position(1, 2, 3, Unit.METRE),
        orientation=Orientation(90, 0, -45, Unit.DEGREE_UNIT_OF_ANGLE),
    )

    ua_frame = pose.to_ua(
        length_unit=Unit.MILLIMETRE,
        angle_unit=Unit.RADIAN,
    )
    result = Pose.from_ua(
        ua_frame,
        length_unit=Unit.MILLIMETRE,
        angle_unit=Unit.RADIAN,
    )

    assert result.position == Position(1000, 2000, 3000, Unit.MILLIMETRE)
    assert result.orientation.a == pytest.approx(math.pi / 2)
    assert result.orientation.b == pytest.approx(0)
    assert result.orientation.c == pytest.approx(-math.pi / 4)
    assert result.orientation.unit == Unit.RADIAN


def test_pose_with_methods_return_updated_copies() -> None:
    pose = Pose()
    new_position = Position(1, 2, 3, Unit.METRE)
    new_orientation = Orientation(10, 20, 30)

    moved = pose.with_position(new_position)
    rotated = pose.with_orientation(new_orientation)

    assert moved.position == new_position
    assert moved.orientation == pose.orientation
    assert rotated.orientation == new_orientation
    assert rotated.position == pose.position
    assert pose == Pose()  # Original remains unchanged.
