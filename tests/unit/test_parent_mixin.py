# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import sys
from pathlib import PurePosixPath
from types import ModuleType
from typing import Annotated, Any, Generic, Literal, Optional, Union

import pytest
from asyncua import ua
from typing_extensions import TypeVar, override

from opensmi.server import UaObject
from opensmi.server.mixins import ParentMixin
from opensmi.server.ua_object import UaObjectDefinition

_T = TypeVar("_T")
_ParentT = TypeVar("_ParentT", bound=UaObject, default="DummyParent")


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


# --- forward references, unions, generics -----------------------------------------------------------------------


class OtherParent(UaObject):
    @override
    async def _get_definition(self) -> UaObjectDefinition:
        raise NotImplementedError


class SubParent(DummyParent):
    pass


class GenericParent(UaObject, Generic[_T]):
    @override
    async def _get_definition(self) -> UaObjectDefinition:
        raise NotImplementedError


class WithStringRef(ParentMixin["DummyParent"], DummyUaObject):
    pass


class WithUnresolvableRef(ParentMixin["OnlyImportedWhileTypeChecking"], DummyUaObject):  # pyright: ignore[reportUndefinedVariable]
    pass


class WithUnion(ParentMixin[Union[DummyParent, "OtherParent"]], DummyUaObject):
    pass


class WithStringUnion(ParentMixin["DummyParent | OtherParent"], DummyUaObject):
    pass


class WithGeneric(ParentMixin[GenericParent[int]], DummyUaObject):
    pass


class WithAnnotated(ParentMixin[Annotated[DummyParent, "meta"]], DummyUaObject):
    pass


class GenericIntermediate(ParentMixin[_ParentT], DummyUaObject):
    pass


class SpecializedSubclass(GenericIntermediate[OtherParent]):  # substitutes the TypeVar
    pass


class InheritsSpec(WithStringRef):
    pass


class NotSpecialized(ParentMixin, DummyUaObject):  # pyright: ignore[reportMissingTypeArgument]
    pass


def _make_module(name: str, source: str) -> ModuleType:
    module = ModuleType(name)
    sys.modules[name] = module
    exec(source, vars(module))
    return module


@pytest.mark.parametrize(
    ("cls", "parent"),
    [
        (WithStringRef, DummyParent()),
        (WithStringRef, SubParent()),
        (WithUnion, DummyParent()),
        (WithUnion, OtherParent()),
        (WithStringUnion, OtherParent()),
        (WithGeneric, GenericParent[int]()),
        (WithAnnotated, DummyParent()),
        (GenericIntermediate, DummyParent()),  # TypeVar default
        (SpecializedSubclass, OtherParent()),
        (InheritsSpec, DummyParent()),
        (NotSpecialized, DummyUaObject()),
    ],
)
def test_accepts_valid_parent(cls: type[DummyUaObject], parent: UaObject) -> None:
    assert cls(parent=parent).parent is parent


@pytest.mark.parametrize(
    ("cls", "parent"),
    [
        (WithStringRef, OtherParent()),
        (WithUnion, DummyUaObject()),
        (WithStringUnion, DummyUaObject()),
        (WithGeneric, DummyParent()),
        (WithAnnotated, OtherParent()),
        (GenericIntermediate, OtherParent()),  # TypeVar default
        (SpecializedSubclass, DummyParent()),
        (InheritsSpec, OtherParent()),
    ],
)
def test_rejects_wrong_parent(cls: type[DummyUaObject], parent: UaObject) -> None:
    with pytest.raises(TypeError, match="Invalid parent"):
        _ = cls(parent=parent)


def test_unresolvable_ref_matches_by_name() -> None:
    only_imported_while_type_checking = type("OnlyImportedWhileTypeChecking", (DummyParent,), {})
    _ = WithUnresolvableRef(parent=only_imported_while_type_checking())  # pyright: ignore[reportArgumentType]

    with pytest.raises(TypeError, match="Expected OnlyImportedWhileTypeChecking, got DummyParent"):
        _ = WithUnresolvableRef(parent=DummyParent())  # pyright: ignore[reportArgumentType]


def test_ref_resolves_in_module_of_declaring_class() -> None:
    other = _make_module(
        "_parent_mixin_other_module",
        "from test_parent_mixin import DummyParent, DummyUaObject\n"
        "from opensmi.server.mixins import ParentMixin\n"
        "class HelperParent(DummyParent): pass\n"
        "class HelperChild(ParentMixin['HelperParent'], DummyUaObject): pass\n",
    )

    class SubclassInOtherModule(other.HelperChild):  # pyright: ignore[reportUntypedBaseClass]
        pass

    _ = SubclassInOtherModule(parent=other.HelperParent())

    # same name, but not the class referenced in the declaring module => evaluation was used, not name-matching
    decoy = type("HelperParent", (DummyParent,), {})
    with pytest.raises(TypeError, match="Invalid parent"):
        _ = SubclassInOtherModule(parent=decoy())


@pytest.mark.parametrize(
    "arg",
    [
        Literal["foo"],
        DummyParent | None,
        Optional["DummyParent"],
        None,
    ],
)
def test_unsupported_type_argument_raises_at_class_definition(arg: Any) -> None:
    with pytest.raises(TypeError):

        class _Invalid(ParentMixin[arg], DummyUaObject):  # pyright: ignore[reportInvalidTypeForm]
            pass
