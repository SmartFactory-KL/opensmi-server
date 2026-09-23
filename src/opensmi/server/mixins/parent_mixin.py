# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Mixin for typing the parent of an `UaObject`."""

from typing import TYPE_CHECKING, Any, ForwardRef, Generic, cast, get_args, get_origin

import structlog
from opensmi.core.base_ua_object import BaseUaObject
from typing_extensions import TypeVar

from opensmi.server._server import Server
from opensmi.server.ua_object import UaObject

if TYPE_CHECKING:
    from opensmi.server.base_machine import BaseMachine


_ParentType = TypeVar("_ParentType", bound=UaObject, default=UaObject)

_LOGGER: structlog.stdlib.BoundLogger = structlog.getLogger(__name__)


def _get_generic_type(cls: type[Any], generic: type[Any]) -> type[Any] | None:
    """Return the concrete type argument used by ``cls`` for ``generic``."""
    for base in cls.__orig_bases__:
        if get_origin(base) is not generic:
            continue

        type_arg = get_args(base)[0]

        if isinstance(type_arg, ForwardRef):
            _LOGGER.warning("Forward reference not supported!", cls=cls, generic=generic, ref=type_arg)
            return None

        if not isinstance(type_arg, type):
            _LOGGER.warning(f"{generic.__name__}[{type_arg!r}] does not contain a runtime-checkable type")
            return None

        return type_arg

    _LOGGER.warning(f"{cls.__name__} does not specialize {generic.__name__}")
    return None


class ParentMixin(Generic[_ParentType]):
    """Mixin for typing the parent of an `UaObject`."""

    _parent: BaseUaObject[Server] | None

    if TYPE_CHECKING:

        def __init__(self, *, parent: _ParentType | None = None, **kwargs: Any) -> None:
            """Type-checking stub for typed ``parent`` parameter."""

    @property
    def parent(self) -> _ParentType:
        """Parent of this `UaObject` instance."""
        assert self._parent is not None, f"Parent was not set yet: {self!r}"
        return cast(_ParentType, self._parent)

    @parent.setter
    def parent(self, parent: BaseUaObject[Server]) -> None:
        """Set the parent of this `UaObject` instance.

        Given ``parent`` will be runtime type-checked against the generic `_ParentType` of `ParentMixin`.
        """
        assert isinstance(parent, UaObject), "No valid parent given, must be at least an UaObject!"

        try:
            parent_type = _get_generic_type(type(self), ParentMixin)
        except TypeError as err:
            err.add_note(f"class={type(self).__name__} instance={self!r}")
            raise

        if parent_type is not None and not isinstance(parent, parent_type):
            msg = (
                f"Invalid parent for '{type(self).__name__}': "
                f"Expected {parent_type.__name__}, got {type(parent).__name__}"
            )
            raise TypeError(msg)

        self._parent = parent

    @property
    def root_parent(self) -> "BaseMachine":
        """Root parent (=a machine) of this ``UaObject``."""
        return self.parent.root_parent  # type: ignore
