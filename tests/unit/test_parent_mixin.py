# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from pathlib import PurePosixPath

import pytest
from asyncua import ua
from typing_extensions import override

from opensmi.server import UaObject
from opensmi.server.mixins import ParentMixin
from opensmi.server.ua_object import UaObjectDefinition


class DummyParent(UaObject):
    @override
    async def _get_definition(self) -> UaObjectDefinition:
        raise NotImplementedError

    @property
    @override
    def path(self) -> PurePosixPath:
        return PurePosixPath("/")


class DummyUaObject(UaObject):
    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(object_type=ua.NodeId())


class DummyUaObjectWithParentType(ParentMixin[DummyParent], DummyUaObject):
    pass


def test_untyped_accepts_valid_parent_at_init() -> None:
    _ = DummyUaObject(parent=DummyParent())


def test_untyped_allows_none_parent_at_init() -> None:
    _ = DummyUaObject(parent=None)  # default, allowed


def test_untyped_rejects_none_reassignment_after_init() -> None:
    # not allowed, makes no sense
    with pytest.raises(AssertionError):
        DummyUaObject().parent = None  # pyright: ignore[reportAttributeAccessIssue]


def test_untyped_rejects_wrong_parent_type_at_init() -> None:
    class Foo:
        pass

    with pytest.raises(AssertionError):
        _ = DummyUaObject(parent=Foo())  # pyright: ignore[reportArgumentType]


def test_typed_accepts_valid_parent_at_init() -> None:
    _ = DummyUaObjectWithParentType(parent=DummyParent())


def test_typed_allows_none_parent_at_init() -> None:
    _ = DummyUaObjectWithParentType(parent=None)  # default, allowed


def test_typed_rejects_none_reassignment_after_init() -> None:
    # not allowed, makes no sense
    with pytest.raises(AssertionError):
        DummyUaObjectWithParentType().parent = None  # pyright: ignore[reportAttributeAccessIssue]


def test_typed_rejects_wrong_parent_type_at_init() -> None:
    with pytest.raises(TypeError):
        _ = DummyUaObjectWithParentType(parent=DummyUaObject())  # pyright: ignore[reportArgumentType]
