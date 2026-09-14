# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from enum import Enum, IntEnum
from pathlib import PurePosixPath
from typing import Final

import pytest
from asyncua import ua
from asyncua.common.node import Node
from opensmi.core.base_server import BaseServer
from opensmi.core.errors import OutOfRangeError
from typing_extensions import override

from opensmi.server import UaObject, UaVariable
from opensmi.server.ua_object import UaObjectDefinition


class MockServer(BaseServer[UaObject]):
    pass


class TestEnum(IntEnum):
    ZERO = 0
    ONE = 1
    TWO = 2


class DummyParent(UaObject):
    @override
    async def _get_definition(self) -> UaObjectDefinition:
        raise NotImplementedError

    @property
    @override
    def path(self) -> PurePosixPath:
        return PurePosixPath("/")


DUMMY_PARENT: Final[DummyParent] = DummyParent()


class DummyUaObject(UaObject):
    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=ua.NodeId())


@pytest.mark.parametrize(
    ("initial_value", "expected_type", "expected_ua_variant_type"),
    [
        (0, int, ua.VariantType.Int64),
        (1.5, float, ua.VariantType.Double),
        ("hello", str, ua.VariantType.String),
        (True, bool, ua.VariantType.Boolean),
        ([1, 2, 3], list, ua.VariantType.Int64),
        (TestEnum.ONE, TestEnum, ua.VariantType.Int32),
    ],
)
def test_types_simple(initial_value, expected_type, expected_ua_variant_type):
    var = UaVariable(initial_value)

    assert var._initial_value_type == expected_type
    assert var.initial_value == initial_value

    assert var._initial_value.Value == initial_value
    assert var._initial_value.VariantType == expected_ua_variant_type


def test_type_ua_object():
    server = MockServer()
    dummy = DummyUaObject()
    dummy.server = server  # pyright: ignore[reportAttributeAccessIssue]
    dummy.ua_node = Node(
        session=None,  # pyright: ignore[reportArgumentType]
        nodeid=ua.NodeId(NamespaceIndex=ua.Int16(1), Identifier=ua.Int32(1)),
    )
    server.register_ua_object(dummy)

    var = UaVariable(dummy)
    var.server = server  # pyright: ignore[reportAttributeAccessIssue]

    assert var._initial_value_type == UaObject
    assert var.initial_value == dummy  # looks internal nodeid up in server

    assert var._initial_value.VariantType == ua.VariantType.NodeId
    assert var._initial_value.Value.nodeid == dummy.ua_node.nodeid


def test_types_invalid_enum() -> None:
    class InvalidEnum(Enum):  # not IntEnum
        ONE = 1
        TWO = 2

    with pytest.raises(TypeError):
        UaVariable(InvalidEnum.ONE)


def test_types_invalid_int_enum() -> None:
    class InvalidIntEnum(IntEnum):  # IntEnum, but does not start with 0
        ONE = 1
        TWO = 2

    with pytest.raises(ValueError, match="OPC UA compatible IntEnums must start at 0 and increase by 1 per member"):
        UaVariable(InvalidIntEnum.ONE)


# ---------------------------------------------------------------------------
# check_range
# ---------------------------------------------------------------------------


def test_check_range_no_range_always_true():
    var = UaVariable(1.5)
    assert var.check_range(0) is True
    assert var.check_range(1_000_000) is True


def test_check_range_within_and_outside_bounds():
    var = UaVariable(5.0, range=(0.0, 10.0))
    assert var.check_range(0.0) is True
    assert var.check_range(10.0) is True
    assert var.check_range(5.0) is True
    assert var.check_range(-0.1) is False
    assert var.check_range(10.1) is False


def test_check_range_unwraps_ua_variant():
    var = UaVariable(5.0, range=(0.0, 10.0))
    assert var.check_range(ua.Variant(Value=3.0)) is True
    assert var.check_range(ua.Variant(Value=30.0)) is False


# ---------------------------------------------------------------------------
# initial_value getter/setter
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("initial_value", "new_value"),
    [
        (0, 5),
        (1.5, 9.5),
        ("hello", "world"),
        (True, False),
        (TestEnum.ONE, TestEnum.TWO),
    ],
)
def test_initial_value_getter_and_setter(initial_value, new_value):
    var = UaVariable(initial_value=initial_value)
    assert var.initial_value == initial_value

    var.initial_value = new_value
    assert var.initial_value == new_value
    assert var._initial_value.Value == new_value


@pytest.mark.parametrize(
    ("initial_value", "new_value"),
    [
        (0, 5),
        (1.5, 9.5),
        ("hello", "world"),
        (TestEnum.ONE, TestEnum.TWO),
    ],
)
def test_initial_value_setter_ua_variant(initial_value, new_value):
    var = UaVariable(initial_value)
    assert var.initial_value == initial_value
    var.initial_value = ua.Variant(Value=new_value)
    assert var.initial_value == new_value


@pytest.mark.parametrize(
    ("initial_value", "wrong_type_value"),
    [
        (0, "not an int"),
        (1.5, "not a float"),
        ("hello", 123),
        (True, "not a bool"),
        (TestEnum.ONE, "not a enum"),
    ],
)
def test_initial_value_setter_rejects_wrong_type(initial_value, wrong_type_value):
    var = UaVariable(initial_value=initial_value)
    with pytest.raises(TypeError):
        var.initial_value = wrong_type_value

    with pytest.raises(TypeError):
        var.initial_value = ua.Variant(wrong_type_value)


# ---------------------------------------------------------------------------
# write_check
# ---------------------------------------------------------------------------


def test_write_check_none_rejected_for_non_str_non_optional():
    var = UaVariable(initial_value=1)
    var.parent = DUMMY_PARENT  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(OutOfRangeError):
        var.write_check(None)


def test_write_check_none_allowed_for_str_type():
    var = UaVariable(initial_value="hello")
    var.parent = DUMMY_PARENT  # pyright: ignore[reportAttributeAccessIssue]
    var.write_check(None)  # should not raise


def test_write_check_none_allowed_when_optional_ok():
    var = UaVariable(initial_value=1, optional_ok=True)
    var.parent = DUMMY_PARENT  # pyright: ignore[reportAttributeAccessIssue]
    var.write_check(None)  # should not raise


def test_write_check_numeric_out_of_range():
    var = UaVariable(initial_value=5.0, range=(0.0, 10.0))
    var.parent = DUMMY_PARENT  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(OutOfRangeError):
        var.write_check(11.0)
    var.write_check(9.0)  # should not raise


def test_write_check_int_enum_invalid_value():
    var = UaVariable(initial_value=TestEnum.ONE)
    var.parent = DUMMY_PARENT  # pyright: ignore[reportAttributeAccessIssue]
    with pytest.raises(OutOfRangeError):
        var.write_check(99)  # pyright: ignore[reportArgumentType]

    # should not raise
    var.write_check(TestEnum.TWO.value)  # pyright: ignore[reportArgumentType]
