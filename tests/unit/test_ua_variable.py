# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from enum import Enum

import pytest
from asyncua import ua
from asyncua.common.node import Node
from opensmi.core.base_server import BaseServer
from typing_extensions import override

from opensmi.server import UaObject, UaVariable
from opensmi.server.ua_object import UaObjectDefinition


class MockServer(BaseServer[UaObject]):
    pass


class TestEnum(Enum):
    ONE = 1
    TWO = 2


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
        (TestEnum.ONE, TestEnum, ua.VariantType.ExtensionObject),
    ],
)
def test_types_simple(initial_value, expected_type, expected_ua_variant_type):
    var = UaVariable(initial_value=initial_value)

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

    var = UaVariable(initial_value=dummy)
    var.server = server  # pyright: ignore[reportAttributeAccessIssue]

    assert var._initial_value_type == UaObject
    assert var.initial_value == dummy  # looks internal nodeid up in server

    assert var._initial_value.VariantType == ua.VariantType.NodeId
    assert var._initial_value.Value.nodeid == dummy.ua_node.nodeid


# def test_unit_without_range_raises():
#     with pytest.raises(AssertionError, match=""):
