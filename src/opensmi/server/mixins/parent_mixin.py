# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""(Optional) Mixin for typing the parent of an `UaObject`."""

import sys
import typing
from types import NoneType, UnionType
from typing import TYPE_CHECKING, Annotated, Any, ForwardRef, Generic, Union, cast, get_args, get_origin

from opensmi.core.base_ua_object import BaseUaObject
from typing_extensions import NoDefault, TypeVar, evaluate_forward_ref

from opensmi.server._server import Server
from opensmi.server.ua_object import UaObject

if TYPE_CHECKING:
    from opensmi.server.base_machine import BaseMachine


_ParentType = TypeVar("_ParentType", bound=UaObject, default=UaObject)

_ParentSpec = tuple[type[Any] | str, ...]
"""Accepted parent types: resolved classes, or class names to match against the parent's MRO."""


def _module_globals(obj: object) -> dict[str, Any]:
    module = sys.modules.get(getattr(obj, "__module__", ""))
    return vars(module) if module is not None else {}


def _resolve_forward_ref(ref: ForwardRef, owner: object, *, evaluate: bool) -> _ParentSpec:
    """Evaluate ``ref`` in the module of ``owner``, falling back to name-matching on ``NameError``."""
    if evaluate:
        try:
            resolved = evaluate_forward_ref(
                ref, owner=owner if isinstance(owner, type) else None, globals=_module_globals(owner)
            )
        except NameError:
            pass
        else:
            return _resolve(resolved, owner, evaluate=evaluate)
    return tuple(name.strip() for name in ref.__forward_arg__.split("|"))


def _resolve(arg: Any, owner: object, *, evaluate: bool) -> _ParentSpec:
    """Resolve the type argument ``arg`` of ``ParentMixin[arg]`` into a `_ParentSpec` (supported forms: `ParentMixin`).

    Forward references are resolved in the module of ``owner``. With ``evaluate=False`` they are never evaluated, which
    validates only the shape (used at class definition, when referenced classes may not exist yet).

    Raises:
        TypeError: If ``arg`` is not supported as parent type.

    """
    if arg is NoneType or arg is None:
        msg = "ParentMixin[...] must not contain None: the parent of an UaObject is never None"
        raise TypeError(msg)

    if isinstance(arg, str):  # e.g. a `TypeVar` default given as string
        arg = ForwardRef(arg)

    if isinstance(arg, ForwardRef):
        return _resolve_forward_ref(arg, owner, evaluate=evaluate)

    if isinstance(arg, typing.TypeVar):  # typing_extensions.TypeVar creates typing.TypeVar instances
        default = getattr(arg, "__default__", NoDefault)
        arg_or_bound = default if default is not NoDefault else (arg.__bound__ or UaObject)
        return _resolve(arg_or_bound, arg, evaluate=evaluate)

    origin = get_origin(arg)

    if origin is Annotated:
        return _resolve(get_args(arg)[0], owner, evaluate=evaluate)

    if origin is Union or origin is UnionType:
        return tuple(t for member in get_args(arg) for t in _resolve(member, owner, evaluate=evaluate))

    if isinstance(origin, type):  # parameterized generic, e.g. `UaObjectContainer[Resource]`
        return (origin,)

    if isinstance(arg, type):
        return (arg,)

    msg = f"ParentMixin[{arg!r}] is not supported, use a class, forward reference or union of these"
    raise TypeError(msg)


def _find_parent_type_arg(cls: type[Any], subst: dict[Any, tuple[Any, type[Any]]]) -> tuple[Any, type[Any]] | None:
    """Find the type argument of ``ParentMixin[...]`` in the bases of ``cls``, together with its declaring class.

    TypeVars are substituted through generic bases, so ``class Foo(Base[X])`` with ``class Base(ParentMixin[T])``
    yields ``X``. ``subst`` maps the TypeVars of ``cls`` to their (argument, declaring class).
    """
    for base in vars(cls).get("__orig_bases__", cls.__bases__):
        origin = get_origin(base) or base
        args = tuple(
            subst.get(a, (a, cls)) if isinstance(a, typing.TypeVar) else (a, cls)  # TypeVars of `cls`
            for a in get_args(base)
        )
        if origin is ParentMixin:
            return args[0] if args else None
        if isinstance(origin, type) and origin is not Generic and issubclass(origin, ParentMixin):
            found = _find_parent_type_arg(origin, dict(zip(getattr(origin, "__parameters__", ()), args, strict=False)))
            if found is not None:
                return found
    return None


def _get_parent_spec(cls: type[Any]) -> _ParentSpec:
    """Return the accepted parent types of ``cls``."""
    arg, owner = _find_parent_type_arg(cls, {}) or (_ParentType, ParentMixin)
    return _resolve(arg, owner, evaluate=True)


def _matches(parent: object, spec: _ParentSpec) -> bool:
    names = {klass.__name__ for klass in type(parent).__mro__}
    return any(isinstance(parent, t) if isinstance(t, type) else t in names for t in spec)


class ParentMixin(Generic[_ParentType]):
    """(Optional) Mixin for typing the parent of an `UaObject`.

    Gives the ``parent`` property the declared type, and checks the type at runtime whenever a parent is assigned::

        class MyComponent(ParentMixin["MyMachine"], NotificationForwarderMixin, BaseComponent):
            async def _init(self) -> None:
                self.parent.do_machine_things()  # typed as `MyMachine`

    As with all mixins, ``ParentMixin`` comes before the base class.

    Supported type arguments
    ========================

    - A class: ``ParentMixin[BaseMachineryItem]``. Subclasses of it are accepted as well.
    - A forward reference: ``ParentMixin["BaseMachineryItem"]``, see below.
    - A union of these: ``ParentMixin[A | B]``, ``ParentMixin[Union["A", B]]`` or ``ParentMixin["A | B"]``.
    - A parameterized generic: ``ParentMixin[UaObjectContainer[Resource]]``. Only the origin (`UaObjectContainer`)
      is checked at runtime.
    - ``Annotated[X, ...]``, which is checked as ``X``.
    - A TypeVar, for generic intermediate classes: ``class Base(ParentMixin[T], ...)``. Subclasses specializing it
      (``class Foo(Base[MyMachine])``) are checked against the given type. Unspecialized, the TypeVar's default,
      bound or `UaObject` is used, in this order.
    - No type argument: any `UaObject` is accepted.

    Forward references
    ==================

    A forward reference is evaluated in the module of the class that declares it. If the name does not exist there at
    runtime, which is the case for classes only imported under ``TYPE_CHECKING`` (e.g. to avoid circular imports),
    the parent is matched by **class name** instead: any class in the MRO of the parent with that ``__name__`` is
    accepted. This check is weaker, since an unrelated class of the same name passes too. A typo in a forward reference
    is reported by the type checker (basedpyright); at runtime, it makes every parent assignment fail.

    Errors
    ======

    - ``TypeError`` at class definition, if the type argument is not supported, e.g. ``Literal[...]`` or anything
      containing ``None`` (``Optional[X]``, ``X | None``).
    - ``TypeError`` on parent assignment, if the parent does not match the type argument.
    - ``AssertionError`` on reading ``parent`` before it was set, or on assigning ``None``: the parent can only be
      left unset (``parent=None``) at construction.
    """

    _parent: BaseUaObject[Server] | None

    if TYPE_CHECKING:

        def __init__(self, *, parent: _ParentType | None = None, **kwargs: Any) -> None:
            """Type-checking stub for typed ``parent`` parameter."""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Validate the shape of the ``ParentMixin[...]`` type argument at class definition."""
        super().__init_subclass__(**kwargs)
        found = _find_parent_type_arg(cls, {})
        if found is not None:
            try:
                _ = _resolve(*found, evaluate=False)
            except TypeError as err:
                err.add_note(f"class={cls.__qualname__}")
                raise

    @property
    def parent(self) -> _ParentType:
        """Parent of this `UaObject` instance."""
        assert self._parent is not None, f"Parent was not set yet: {self!r}"
        return cast(_ParentType, self._parent)

    @parent.setter
    def parent(self, parent: BaseUaObject[Server]) -> None:
        """Set the parent of this `UaObject` instance.

        Given ``parent`` is runtime type-checked against the type argument of `ParentMixin`.

        Raises:
            TypeError: If ``parent`` does not match the type argument.


        """
        assert isinstance(parent, UaObject), "No valid parent given, must be at least an UaObject!"

        try:
            spec = _get_parent_spec(type(self))
        except TypeError as err:
            err.add_note(f"class={type(self).__name__} instance={self!r}")
            raise

        if not _matches(parent, spec):
            expected = " | ".join(t if isinstance(t, str) else t.__name__ for t in spec)
            msg = f"Invalid parent for '{type(self).__name__}': Expected {expected}, got {type(parent).__name__}"
            raise TypeError(msg)

        self._set_parent(parent)

    @property
    def root_parent(self) -> "BaseMachine":
        """Root parent (=a machine) of this ``UaObject``."""
        return self.parent.root_parent  # type: ignore
