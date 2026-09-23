# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Generic container for `UaObject`. Is itself an `UaObject`."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from typing import TYPE_CHECKING, Generic, TypeVar, override

from asyncua import ua

from opensmi.server.mixins.parent_mixin import ParentMixin
from opensmi.server.nodesets import MachineryNodeIds, SmartFactoryMachineSetNodeIds, SmartFactorySkillSetNodeIds
from opensmi.server.resource import Resource, ResourceDefinition
from opensmi.server.ua_object import UaObject, UaObjectDefinition

_ObjectType = TypeVar("_ObjectType", bound=UaObject)

if TYPE_CHECKING:
    from opensmi.server.base_machinery_item import BaseComponent as BaseComponent
    from opensmi.server.base_machinery_item import BaseMachineryItem as BaseMachineryItem
    from opensmi.server.base_method import BaseMethod as BaseMethod
    from opensmi.server.base_skill import BaseSkill as BaseSkill
    from opensmi.server.user import User as User


class UaObjectContainer(UaObject, Generic[_ObjectType]):
    """Abstract generic container for `UaObject`. Is itself an `UaObject`. Transparent to its children."""

    def __init__(
        self,
        name: str,
        minimum_access_level: int | None = None,
        bypass_lock: bool = False,
        parent: UaObject | None = None,
    ) -> None:
        """*Cooperative* constructor."""
        super().__init__(
            name=name,
            minimum_access_level=minimum_access_level,
            bypass_lock=bypass_lock,
            parent=parent,
        )
        self._children: dict[str, _ObjectType] = {}

    #
    # Pythonic container interface
    #

    def __getattr__(self, key: str) -> _ObjectType:
        """Return a child object by attribute name.

        :raises AttributeError: If not found.
        """
        try:
            return self._children[key]
        except KeyError as err:
            raise AttributeError(err) from None

    def __getitem__(self, name: str) -> _ObjectType:
        """Get a `UaObject` by the given ``name``.

        :raises KeyError: If no object with given name exists.
        """
        child = self._children.get(name)
        if child is not None:
            return child
        msg = f"No UaObject named '{name}' contained in {self.path}!"
        raise KeyError(msg)

    def __setitem__(self, name: str, value: _ObjectType) -> None:
        """Add a child object by given ``name``.

        :raises KeyError: If the name already exists.
        """
        if name in self._children:
            msg = f"UaObject named '{name}' already exists in {self.path}!"
            raise KeyError(msg)
        self._children[name] = value

    def __contains__(self, key: object) -> bool:
        """Whether an object with given ``name`` exists in this container."""
        return key in self._children

    def __len__(self) -> int:
        """Return the number of contained `UaObject`."""
        return len(self._children)

    def __iter__(self) -> Iterator[_ObjectType]:
        """Iterate over all contained `UaObject`."""
        return iter(self._children.values())


class Resources(ParentMixin["BaseMachineryItem"], UaObjectContainer[Resource]):
    """Management of resources."""

    def __init__(self, parent: BaseMachineryItem) -> None:
        """Initialize the resources collection synchronously."""
        super().__init__(name="Resources", parent=parent)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=SmartFactoryMachineSetNodeIds.ResourcesType,
            namespace_uri=SmartFactoryMachineSetNodeIds,
            reference_type=ua.object_ids.ObjectIds.HasAddIn,
        )

    async def add(self, definition: ResourceDefinition) -> Resource:
        """Add a new resource according to the given ``definition``."""
        new_resource = Resource(definition=definition, parent=self)  # pyright: ignore[reportArgumentType]
        await new_resource.ua_create_node(self.ua_node)
        await new_resource.init()

        self._children[new_resource.name] = new_resource

        return new_resource

    def get_all(self, *, resource_class: str) -> Iterable[Resource]:
        """Get all resources with the given ``resource_class``."""
        return [res for res in self._children.values() if res.resource_class == resource_class]


class SkillSet(ParentMixin["BaseMachineryItem"], UaObjectContainer["BaseSkill"]):
    """Collection of skills."""

    def __init__(self, parent: BaseMachineryItem) -> None:
        """Initialize the skill set synchronously."""
        super().__init__(name="SkillSet", parent=parent)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=SmartFactorySkillSetNodeIds.SkillSetType,
            namespace_uri=SmartFactorySkillSetNodeIds,
            reference_type=ua.object_ids.ObjectIds.HasAddIn,
        )


class MethodSet(ParentMixin["BaseMachineryItem"], UaObjectContainer["BaseMethod"]):
    """Collection of methods."""

    def __init__(self, parent: BaseMachineryItem) -> None:
        """Initialize the method set synchronously."""
        super().__init__(name="MethodSet", parent=parent)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=SmartFactorySkillSetNodeIds.MethodSetType,
            namespace_uri=SmartFactorySkillSetNodeIds,
            reference_type=ua.object_ids.ObjectIds.HasAddIn,
        )


class Components(ParentMixin["BaseMachineryItem"], UaObjectContainer["BaseComponent"]):
    """Collection of components."""

    def __init__(self, *, parent: BaseMachineryItem) -> None:
        """Initialize the components collection synchronously."""
        super().__init__(name="Components", parent=parent)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=MachineryNodeIds.MachineComponentsType,
            namespace_uri=SmartFactoryMachineSetNodeIds,
            reference_type=ua.object_ids.ObjectIds.HasAddIn,
        )


class Users(ParentMixin["BaseMachineryItem"], UaObjectContainer["User"]):
    """Collection of users."""

    def __init__(self, parent: BaseMachineryItem) -> None:
        """Initialize the users collection synchronously."""
        super().__init__(name="Users", parent=parent)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=SmartFactoryMachineSetNodeIds.UsersType,
            namespace_uri=SmartFactoryMachineSetNodeIds,
        )
