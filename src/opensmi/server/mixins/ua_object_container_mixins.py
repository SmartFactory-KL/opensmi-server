# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixins for various `UaObjectContainer` containing `UaObject`."""

from abc import abstractmethod
from collections.abc import AsyncGenerator
from typing import Any, Generic, TypeVar, cast, get_type_hints

import structlog
from opensmi.core.lifecycle_mixin import lifecycle
from opensmi.core.meta import resolve_generic_arguments

from opensmi.server.base_machinery_item import BaseMachineryItem
from opensmi.server.ua_object_containers import Components, MethodSet, Resources, SkillSet

_LOGGER = structlog.get_logger(__name__)


def _validate_types(*, cls: type, target: type, obj: Any) -> None:
    """Validate ``obj`` attributes against the resolved type annotations."""
    types: tuple[type, ...] | None = resolve_generic_arguments(cls, target)
    assert types is not None
    assert len(types) == 1, f"Expected 1 type, got {len(types)}"

    try:
        annotations = get_type_hints(types[0])
        for name, annotation in annotations.items():
            if name.startswith("_"):  # skip private attributes
                continue

            attr = getattr(obj, name, None)
            assert isinstance(attr, annotation), f"{name} is not of type {annotation}!"
    except NameError as err:
        _LOGGER.warning("Can not validate types", reason=err, cls=cls, target=target, obj=obj)


class ResourcesMixin:
    """Mixin for `BaseMachineryItem` with `Resources`."""

    __resources: Resources | None = None

    @abstractmethod
    async def _init_resources(self) -> None:
        pass

    @lifecycle(before=BaseMachineryItem.lifecycle_machinery_item)
    async def lifecycle_resources(self) -> AsyncGenerator[None]:
        """Initialize the resources `UaObjectContainer`."""
        self.resources.parent = self  # pyright: ignore[reportAttributeAccessIssue]
        await self.resources.ua_create_node(self.ua_node)  # pyright: ignore[reportAttributeAccessIssue]
        await self.resources.init()

        await self._init_resources()

        yield
        # no shutdown

    @property
    def resources(self) -> Resources:
        """Return the `Resources` container."""
        if self.__resources is None:
            self.__resources = Resources()
        return self.__resources


#################################################################################################################

SkillSetType = TypeVar("SkillSetType", bound="SkillSet")


class SkillSetMixin(Generic[SkillSetType]):
    """Mixin for `BaseMachineryItem` with typed `SkillSet`. Automatically validates provided type hints."""

    _skill_set: SkillSet  # without type hints

    @lifecycle(after=BaseMachineryItem.lifecycle_machinery_item)
    async def _lifecycle_validate_skill_set_types(self) -> AsyncGenerator[None]:
        _validate_types(cls=type(self), target=SkillSetMixin, obj=self.skill_set)
        yield
        # no shutdown

    @property
    def skill_set(self) -> SkillSetType:
        """Return typed skill set `UaObjectContainer`. Read-only property."""
        return cast(SkillSetType, self._skill_set)


#################################################################################################################

MethodSetType = TypeVar("MethodSetType", bound="MethodSet")


class MethodSetMixin(Generic[MethodSetType]):
    """Mixin for `BaseMachineryItem` with typed `MethodSet`. Automatically validates provided type hints."""

    _method_set: MethodSet  # without type hints

    @lifecycle(after=BaseMachineryItem.lifecycle_machinery_item)
    async def _lifecycle_validate_method_set_types(self) -> AsyncGenerator[None]:
        _validate_types(cls=type(self), target=MethodSetMixin, obj=self.method_set)
        yield
        # no shutdown

    @property
    def method_set(self) -> MethodSetType:
        """Return typed method set `UaObjectContainer`. Read-only property."""
        return cast(MethodSetType, self._method_set)


#################################################################################################################

ComponentsType = TypeVar("ComponentsType", bound="Components")


class ComponentsMixin(Generic[ComponentsType]):
    """Mixin for `BaseMachineryItem` with typed `Components`. Automatically validates provided type hints."""

    _components: Components  # without type hints

    @lifecycle(after=BaseMachineryItem.lifecycle_machinery_item)
    async def _lifecycle_validate_components_types(self) -> AsyncGenerator[None]:
        _validate_types(cls=type(self), target=ComponentsMixin, obj=self.components)
        yield
        # no shutdown

    @property
    def components(self) -> ComponentsType:
        """Return typed components `UaObjectContainer`. Read-only property."""
        return cast(ComponentsType, self._components)
