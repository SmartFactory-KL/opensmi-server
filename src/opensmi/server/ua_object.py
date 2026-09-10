# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Base class for all server-side PyUaAdapter-managed OPC UA objects."""

from abc import abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, final

from asyncua import ua
from asyncua.common.node import Node
from opensmi.core.base_ua_object import BaseUaObject
from opensmi.core.lifecycle_mixin import LifecycleMixin
from opensmi.core.protocols import NamespaceProvider
from opensmi.core.repr_mixin import ReprStrMixin
from opensmi.core.ua import NodeIdDefinition
from opensmi.core.ua_node_util import get_child_without_ns, get_node_id
from typing_extensions import override

from opensmi.server.interfaces import AbstractUaObject
from opensmi.server.protocols import UserAuthorization

if TYPE_CHECKING:
    from opensmi.server import Lock
    from opensmi.server import Server as Server


@dataclass(slots=True, kw_only=True)
class UaObjectDefinition:
    """Instantiation definition for a `UaObject`."""

    object_type: ua.NodeId | NodeIdDefinition
    """OPC UA object type."""
    namespace_uri: type[NamespaceProvider] | str | None = None
    """Namespace URI for the OPC UA browse name to use instead of the default namespace index 1."""
    instantiate_optional: bool = False
    """Whether to always instantiate all modeled optional sub nodes as well."""
    reference_type: ua.NodeId | int | None = None
    """OPC UA reference type to use. Defaults to ``HasComponent`` for objects."""


class UaObject(BaseUaObject["Server"], LifecycleMixin, ReprStrMixin, AbstractUaObject):
    """Abstract base class for all server-side PyUaAdapter-managed OPC UA objects."""

    def __init__(
        self,
        *,
        name: str | None = None,
        minimum_access_level: int | None = None,
        bypass_lock: bool = False,
        server: "Server | None" = None,
        **kwargs: dict[str, Any],
    ) -> None:
        """*Cooperative* constructor.

        :param name: Name of the UaObject. Must be a non-empty string or ``None`` to use the Python class name.
        :param minimum_access_level: Minimum access level of the UaObject (write/call). Read access is always allowed.
        :param bypass_lock: Whether the variable can be written to without ownership of corresponding lock.
        """
        super().__init__(name=name, server=server, **kwargs)

        self.minimum_access_level: int = 1 if minimum_access_level is None else int(minimum_access_level)
        """Minimum `User` access level required for write & method execution."""
        self.bypass_lock: bool = bypass_lock
        """Whether `Lock` ownership can be bypassed."""

    @abstractmethod
    async def _get_definition(self) -> UaObjectDefinition:
        """Return the instantiation definition for the main OPC UA node. Is called from `ua_create_node`."""
        raise NotImplementedError

    async def _ua_create_node(
        self,
        ua_location: Node,
        *,
        instantiate_optional: bool,
        exist_ok: bool,
        remove_existing: bool,
    ) -> None:
        """Get the OPC UA object type and create a new object at the given OPC UA ``ua_location``."""
        definition = await self._get_definition()
        type_ = definition.object_type
        if isinstance(type_, NodeIdDefinition):
            type_ = self.server.ua_get_node_id(type_)
        ns_index = self.server.ua_get_namespace_index(definition.namespace_uri) if definition.namespace_uri else 1
        browse_name = ua.QualifiedName(Name=self.name, NamespaceIndex=ns_index)
        instantiate_optional = instantiate_optional or definition.instantiate_optional

        async def _create_node() -> None:
            node_id = get_node_id(ua_location, name=self.name)

            self.ua_node = await ua_location.add_object(
                nodeid=node_id,
                bname=browse_name,
                objecttype=type_,  # type: ignore
                instantiate_optional=instantiate_optional,
            )

            self.logger.debug(
                "Created main OPC UA node",
                instantiate_optional=instantiate_optional,
                ua_type=type_.to_string(),
                ua_location=ua_location.nodeid.to_string(),
                ua_node=self._ua_node.nodeid.to_string(),  # pyright: ignore[reportOptionalMemberAccess]
            )

        try:
            existing_node = await get_child_without_ns(ua_location, display_name=browse_name.Name)
            if exist_ok:
                self.logger.debug(
                    "Reusing existing OPC UA node",
                    ua_location=ua_location.nodeid.to_string(),
                    ua_node=existing_node.nodeid.to_string(),
                )
                self.ua_node = existing_node
            elif remove_existing:
                deleted_nodes = await existing_node.delete(recursive=True)
                self.logger.debug(
                    "Removed existing OPC UA node(s)",
                    ua_location=ua_location.nodeid.to_string(),
                    ua_node=existing_node.nodeid.to_string(),
                    deleted_nodes=deleted_nodes,
                )
                await _create_node()
            else:
                msg = (
                    f"Found existing node '{existing_node.nodeid.to_string()}', but not allowed to reuse or remove it!"
                )
                raise RuntimeError(msg)
        except ua.uaerrors.BadNoMatch:
            await _create_node()

        if definition.reference_type:
            # This adding and deleting is only required because asyncua does not allow to specify ourselves :/
            # asyncua also ignores modeled references, so for existing nodes we also have to correct it
            reference_type = definition.reference_type
            if isinstance(reference_type, int):
                reference_type = ua.FourByteNodeId(ua.Int32(reference_type))
            await ua_location.add_reference(target=self.ua_node, reftype=reference_type)
            await ua_location.delete_reference(
                target=self.ua_node, reftype=ua.FourByteNodeId(ua.Int32(ua.object_ids.ObjectIds.HasComponent))
            )
            self.logger.debug("Corrected reference to main OPC UA node", reference_type=reference_type)

    @final
    async def ua_create_node(
        self,
        ua_location: Node,
        *,
        instantiate_optional: bool = False,
        exist_ok: bool = False,
        remove_existing: bool = False,
    ) -> None:
        """Create main OPC UA node and all modeled mandatory sub nodes at the given OPC UA ``ua_location``.

        If subclasses want to customize the node creation behavior, override `_ua_create_node()`.

        :param ua_location: Parent OPC-UA node, ideally a folder type.
        :param instantiate_optional: Whether to instantiate all modeled optional sub nodes as well.
        :param exist_ok: Whether to reuse an existing node if found.
        :param remove_existing: Whether to remove existing node if found.

        :raise RuntimeError: If main OPC UA node already set, or already exists and reusing is not allowed.
        """
        assert isinstance(ua_location, Node)
        if self._ua_node is not None:
            msg = f"Main OPC UA node for {self.path} already set!"
            raise RuntimeError(msg)

        await self._ua_create_node(
            ua_location=ua_location,
            instantiate_optional=instantiate_optional,
            exist_ok=exist_ok,
            remove_existing=remove_existing,
        )

    @property
    @override
    def lock(self) -> "Lock":
        """`Lock` instance protecting this `UaObject`."""
        from opensmi.server import Lock

        # check if we have a lock ourselves
        lock = getattr(self, "_lock", None)
        if isinstance(lock, Lock):
            return lock

        # check if we have a parent and ask there. Machines must have a lock -> there is at least one in the chain
        parent = getattr(self, "parent", None)
        if parent is not None:
            parent_lock = getattr(parent, "lock", None)
            if parent_lock is not None:
                return parent_lock  # type: ignore
            msg = f"{self.path} has no lock and no parent which contains a lock!"
            raise RuntimeError(msg)
        msg = f"{self.path} has no lock and has no parent!"
        raise RuntimeError(msg)

    @override
    def access_allowed(self, user: UserAuthorization, *, lock_required: bool | None = None) -> bool:
        if lock_required is None:
            lock_required = not self.bypass_lock

        return self.lock.access_allowed(user, lock_required=lock_required)

    @override
    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from super()._repr_items()
        yield "minimum_access_level", self.minimum_access_level
        yield "bypass_lock", self.bypass_lock
