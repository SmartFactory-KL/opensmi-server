# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Abstraction-layer for OPC UA variables our framework users interacts with."""

import asyncio
import dataclasses
import inspect
from collections.abc import AsyncGenerator, Awaitable, Callable, Iterator, Mapping
from datetime import UTC, datetime, timedelta
from enum import Enum, IntEnum
from typing import Any, Generic

from asyncua import ua
from asyncua.common.node import Node
from opensmi.core import Unit
from opensmi.core.errors import NoValidValueReadError, OutOfRangeError
from opensmi.core.lifecycle_mixin import lifecycle
from opensmi.core.ua_node_util import (
    NULL_NODE_ID,
    get_node_id,
    get_properties,
    write_range,
    write_unit,
    write_value,
)
from typing_extensions import TypeVar, override

from opensmi.server.mixins.notification_mixins import NotificationForwarderMixin
from opensmi.server.structures import _check_structure_value, _fields, _structure_class
from opensmi.server.ua_dictionary_entry import DictionaryEntry
from opensmi.server.ua_object import UaObject, UaObjectDefinition

_VariableType = TypeVar("_VariableType")
"""Python-native variable type of the `UaVariable` (e.g. ``int``, ``string``, ...)."""


def _get_initial_value_and_type(
    initial_value: _VariableType | None, variable_type: type[_VariableType] | None
) -> tuple[ua.Variant, type[_VariableType]]:
    if variable_type is not None and issubclass(variable_type, UaObject):
        initial_value_type = variable_type
        node_id = NULL_NODE_ID
        if isinstance(initial_value, UaObject):
            node_id = initial_value.ua_node.nodeid
        elif isinstance(initial_value, ua.Variant):
            assert initial_value.VariantType == ua.VariantType.NodeId
            node_id = initial_value.Value

        return ua.Variant(Value=node_id, VariantType=ua.VariantType.NodeId), initial_value_type

    if isinstance(initial_value, UaObject):
        return ua.Variant(Value=initial_value.ua_node, VariantType=ua.VariantType.NodeId), UaObject  # pyright: ignore[reportReturnType]

    if isinstance(initial_value, ua.Variant):
        match initial_value.VariantType:  # only variant types that allow None are a problem
            case ua.VariantType.String:
                return initial_value, str  # pyright: ignore[reportReturnType]
            case _:
                return initial_value, type(initial_value.Value)

    if initial_value == [] and _structure_class(initial_value, variable_type) is not None:
        # an empty list has no element to guess the type from
        return ua.Variant(Value=[], VariantType=ua.VariantType.ExtensionObject), list  # pyright: ignore[reportReturnType]

    if initial_value is not None:
        return ua.Variant(Value=initial_value), type(initial_value)

    msg = f"Please provided either a non-Null {initial_value=} or a compatible {variable_type=}!"
    raise ValueError(msg)


def _validate_enum_type(enum_type: type[Enum]) -> None:
    """Ensure given ``enum_type`` is an `IntEnum`, the only kind of enum an OPC UA variable can hold.

    Whether its values fit an enumeration DataType the server creates is checked when it is created
    (`Server.ensure_enum`); an enum bound to an existing DataType (`Server.register_enum`) may have any values.
    """
    if not issubclass(enum_type, IntEnum):
        msg = "Enums are only supported as IntEnums!"
        raise TypeError(msg)


def _pascal_case(field_name: str) -> str:
    """Return the browse name of a dataclass field: ``in_tolerance`` -> ``InTolerance``."""
    return "".join(part[:1].upper() + part[1:] for part in field_name.split("_"))


class UaVariable(NotificationForwarderMixin, UaObject, Generic[_VariableType]):
    """OPC UA Variable for use in `ParameterSet`, `Monitoring`, `FinalResultData`, etc.

    Abstracts raw OPC UA/``asyncua`` away and provides a simple, high-level interface.

    **Complex variables.** A variable whose value is a dataclass instance (not a list) holds the whole structure and
    has one child variable per field (``HasComponent``), recursively for nested dataclasses. A list, also of
    dataclasses, is one leaf. The browse name of a child is its field name in PascalCase (``in_tolerance`` ->
    ``InTolerance``), its display name is the field name. Two fields that become one name fail when the nodes are
    created. Children are reached with `children` or ``variable["Dimensions"]["Length"]`` and inherit ``writable``,
    ``bypass_lock`` and ``minimum_access_level``; `set_writable` and `initial_value` on the parent reach them too.
    They have no ``write_check_callback`` of their own.

    Parent and children always agree. Each write is checked once, at the parent: a write to the parent runs its
    `write_check`, a write to a child runs the parent's `write_check` on the merged value, so a
    ``write_check_callback`` always sees the whole structure. A refused write changes no node. After a passed
    check, the written node, its ancestors and its descendants get their values in one call with one
    ``SourceTimestamp`` and the status of the write; with a bad status, no node has a value. This holds for writes
    through `write` and for external OPC UA writes; a write that bypasses the access control (for example one
    to the node of a child with plain asyncua inside the server) leaves them out of step. A ``write_check_callback``
    only checks and must not write any variable: a write to the same complex variable hangs.
    """

    _initial_value_type: type[_VariableType]
    _initial_value: ua.Variant
    _unit: ua.EUInformation | None
    _write_check_callback: Callable[[_VariableType | None], bool | Awaitable[bool | None] | None] | None

    def __init__(
        self,
        initial_value: _VariableType | None,
        *,
        unit: Unit | str | int | ua.EUInformation | None = None,
        range: tuple[float | int, float | int] | ua.Range | None = None,  # noqa: A002
        minimum_access_level: int | None = None,
        writable: bool = False,
        historize: bool = False,
        bypass_lock: bool = False,
        variable_type: type[_VariableType] | None = None,
        optional_ok: bool = False,
        name: str | None = None,
        write_check_callback: Callable[[_VariableType | None], bool | Awaitable[bool | None] | None] | None = None,
        dictionary_entry: DictionaryEntry | None = None,
        description: str | None = None,
        ua_value_rank: ua.ValueRank | int | None = None,
        ua_data_type: ua.NodeId | int | None = None,
        display_name: str | None = None,
        **kwargs,
    ) -> None:
        """*Cooperative* constructor for a new variable instance.

        :param initial_value: The initial value of the variable. This should not be ``None`` for both correct type hints
            and correctly guessed OPC UA data type of the corresponding OPC UA variable node.
        :param unit: The engineering unit of the variable. Can either be an `Unit`, an integer
            (= OPC UA `ua.EUInformation.UnitId`, e.g. 4410708), or a string (symbol = OPC UA display name, e.g. "cm").
            If provided, the range ``range_`` parameter is mandatory!
        :param range: Tuple containing the minimum and maximum values of the variable.
        :param minimum_access_level: Minimum access level required to write to the variable (externally via OPC UA).
        :param writable: Whether the variable is writable externally or not. Always writable internally.
        :param historize: Whether to keep a history of past values of this variable.
        :param bypass_lock: Whether the variable can be written to without ownership of corresponding lock.
        :param variable_type: The Python type of the variable. Is only required if type cannot be determined from
            ``initial_value``, i.e. when ``None`` is provided.
        :param write_check_callback: (Optional) User-definable function that is called whenever a `write_check`
            occurs (Triggered by internal and external OPC UA writes). Must raise `OutOfRangeError` (+custom message),
            raise any appropriate `UaStatusCodeError` or return ``False`` when the custom check fails. It only checks
            the value and must not write any variable: a write to the same complex variable hangs.
        :param dictionary_entry: (Optional) semantic information via OPC UA dictionary entry.
        :param description: (Optional) A human-readable description for the variable. Prefer a docstring right after
            the variable in its container class instead, which takes precedence and is shown by IDEs. Without both, an
            existing description (e.g. from the node set) is kept, else a warning is logged.
        :param ua_value_rank: (Optional) OPC UA value rank of this variable. **Advanced**
        :param ua_data_type: (Optional) OPC UA data type of the variable. **Advanced**, make sure this type maps to
            the Python type! You could use this for UInt16, etc. instead of default Int64 types
        :param display_name: (Optional) OPC UA display name of the variable. Defaults to the name.
        """
        super().__init__(minimum_access_level=minimum_access_level, bypass_lock=bypass_lock, name=name, **kwargs)

        self._initial_value, self._initial_value_type = _get_initial_value_and_type(initial_value, variable_type)
        self._variant_type = self._initial_value.VariantType
        assert self._initial_value.VariantType != ua.VariantType.Null
        if issubclass(self._initial_value_type, Enum):
            _validate_enum_type(self._initial_value_type)

        if isinstance(unit, Unit):
            self._unit = unit.ua_eu_information
        elif isinstance(unit, str):
            self._unit = Unit.from_symbol(unit).ua_eu_information
        elif isinstance(unit, int):
            self._unit = Unit.from_ua_unit_id(unit).ua_eu_information
        elif isinstance(unit, ua.EUInformation):
            self._unit = unit
        else:
            assert unit is None, f"Given {unit} is neither str, int, ua.EUInformation nor None!"
            self._unit = unit

        if isinstance(range, ua.Range):
            range = (range.Low, range.High)  # noqa: A001
        self._range: tuple[float | int, float | int] | None = range
        self._writable: bool = writable
        self._historize: bool = historize
        self._references: set[UaObject] = set()
        self.optional_ok: bool = optional_ok
        self._write_check_callback = write_check_callback
        self._dictionary_entry: DictionaryEntry | None = dictionary_entry
        self.description: str | None = description

        # customization for _ua_create_node
        self._ua_value_rank: int | None = ua_value_rank
        self._ua_data_type: ua.NodeId | int | None = ua_data_type
        self._variable_type = variable_type
        self._structure: type | None = _structure_class(self._initial_value.Value, variable_type)
        """The dataclass of a structure value, or of the elements of a list of them; checked on every write."""
        self._structure_array: bool = isinstance(self._initial_value.Value, list)
        if self._structure is not None and self._structure_array and self._ua_value_rank is None:
            self._ua_value_rank = ua.ValueRank.OneDimension
        self._display_name = display_name

        # complex variables, see class docstring
        self._children: dict[str, UaVariable[Any]] = {}
        self._complex_parent: UaVariable[Any] | None = None
        self._field_name = ""
        self._tree_lock = asyncio.Lock()

    @lifecycle
    async def _init(self) -> AsyncGenerator[None]:
        await self._write_display_name()
        await self._create_children()

        try:
            await self.write(self._initial_value)
        except ua.uaerrors.UaError as err:
            self.logger.warning("Could not write initial value", value=self._initial_value, reason=err)

        if self._unit is not None:
            await write_unit(self.ua_node, self._unit)

        if self._range is not None:
            await write_range(self.ua_node, self._range)

        if self._writable:  # ua nodes are not writable by default
            await self.set_writable(self._writable)

        # deliberately after writing initial value
        if self._historize:
            await self.set_historize(self._historize)

        # Note: references added after we are initialized are created immediately
        for ref in self._references:
            await self._ua_add_reference(ref)

        if self._dictionary_entry is not None:
            await self.add_dictionary_entry(self._dictionary_entry)
            del self._dictionary_entry  # no longer required

        # precedence: docstring > `description` parameter > existing description (e.g. from the node set)
        if self.description:
            await self.write_description(self.description)
        elif not await self._has_existing_description():
            self.logger.warning("Missing description", path=str(self.path))

        yield
        # no shutdown

    async def _write_display_name(self) -> None:
        if self._display_name is not None:
            await self.ua_node.write_attribute(
                ua.attribute_ids.AttributeIds.DisplayName,
                ua.DataValue(ua.Variant(ua.LocalizedText(self._display_name))),
            )

    async def _create_children(self) -> None:
        """Create a child variable per field if the value is a dataclass instance."""
        value, structure = self._initial_value.Value, self._structure
        if structure is None or self._structure_array:
            return
        for each in _fields(structure):
            field_value = getattr(value, each.name)
            if each.variant_type == ua.VariantType.ExtensionObject:  # nested structure, or an array of them
                arguments: dict[str, Any] = {"initial_value": field_value, "variable_type": each.element}
                if not each.array:
                    del arguments["variable_type"]
            else:
                arguments = {"initial_value": ua.Variant(field_value, each.variant_type)}
                if each.array:
                    arguments["ua_value_rank"] = ua.ValueRank.OneDimension
            child = UaVariable(
                name=_pascal_case(each.name),
                display_name=each.name,
                writable=self._writable,
                bypass_lock=self.bypass_lock,
                minimum_access_level=self.minimum_access_level,
                description=f"Field {each.name} of {structure.__name__}.",
                **arguments,
            )
            child._complex_parent, child._field_name = self, each.name
            child.parent = self
            await child.ua_create_node(self.ua_node)
            self._children[child.name] = child
            await child.init()

    @property
    def children(self) -> Mapping[str, "UaVariable[Any]"]:
        """The child variables of a complex variable by name, empty for any other variable."""
        return self._children

    def __getitem__(self, name: str) -> "UaVariable[Any]":
        """Get a child variable by the given ``name``.

        :raises KeyError: If no child with given name exists.
        """
        try:
            return self._children[name]
        except KeyError:
            msg = f"No child named '{name}' contained in {self.path}!"
            raise KeyError(msg) from None

    @property
    def tree_lock(self) -> asyncio.Lock:
        """Lock that is held while a write to a variable of this complex tree is checked and written."""
        return self._tree_lock if self._complex_parent is None else self._complex_parent.tree_lock

    async def _changes_up(self, value: object) -> list[tuple["UaVariable[Any]", object]]:
        """Return ``value`` for this variable and the merged value for each ancestor, the root last."""
        changes: list[tuple[UaVariable[Any], object]] = [(self, value)]
        node = self
        while (parent := node._complex_parent) is not None:
            value = dataclasses.replace(await parent.ua_node.read_value(), **{node._field_name: value})
            changes.append((parent, value))
            node = parent
        return changes

    async def _check_at_root(self, value: object) -> None:
        """Check a write to a child once, at the root of its complex variable, on the merged value."""
        if self._complex_parent is not None:
            root, merged = (await self._changes_up(value))[-1]
            await root.write_check(merged)  # pyright: ignore[reportArgumentType]

    def _changes_down(self, value: object) -> list[tuple["UaVariable[Any]", object]]:
        """Return the part of ``value`` for each descendant; no value (a bad status) has no parts either."""
        changes: list[tuple[UaVariable[Any], object]] = []
        for child in self._children.values():
            part = None if value is None else getattr(value, child._field_name)  # noqa: SLF001
            changes.append((child, part))
            changes.extend(child._changes_down(part))  # noqa: SLF001
        return changes

    async def sync_writes(self, write_value: ua.WriteValue) -> list[ua.WriteValue]:
        """Return ``write_value`` for this variable and the writes that bring the rest of its tree in step with it.

        All carry one ``SourceTimestamp`` and the ``StatusCode`` of ``write_value``, so a bad status reaches every node.
        A variable that is not part of a complex variable returns ``write_value``.
        """
        if self._complex_parent is None and not self._children:
            return [write_value]
        value = write_value.Value.Value.Value
        timestamp = write_value.Value.SourceTimestamp or datetime.now(UTC)
        changes = [*await self._changes_up(value), *self._changes_down(value)]
        write_values = [
            ua.WriteValue(
                NodeId=node.ua_node.nodeid,
                AttributeId=ua.attribute_ids.AttributeIds.Value,
                Value=ua.DataValue(
                    ua.Variant() if part is None else ua.Variant(part, node._initial_value.VariantType),  # noqa: SLF001
                    StatusCode=write_value.Value.StatusCode,
                    SourceTimestamp=timestamp,  # pyright: ignore[reportArgumentType]
                ),
            )
            for node, part in changes[1:]
        ]
        write_value.Value.SourceTimestamp = timestamp
        return [write_value, *write_values]

    async def _has_existing_description(self) -> bool:
        """Whether the OPC UA node already has a real description, e.g. from the node set."""
        description = (await self.ua_node.read_description()).Text
        # asyncua uses the browse name as placeholder description for new nodes
        return bool(description) and description != (await self.ua_node.read_browse_name()).Name

    @property
    def initial_value(self) -> _VariableType:
        """Initial value of the variable."""
        if isinstance(self._initial_value, ua.Variant):
            return self._extract_value(self._initial_value.Value)
        return self._extract_value(self._initial_value)

    @initial_value.setter
    def initial_value(self, value: _VariableType | ua.Variant) -> None:
        if isinstance(value, ua.Variant):
            value = value.Value

        if not isinstance(value, self._initial_value_type):
            msg = f"Type of {value} ({type(value)}) is not the same as previous type: {self._initial_value_type}"
            raise TypeError(msg)

        previous = self._initial_value
        value = ua.uatypes.get_default_value(self._initial_value.VariantType) if value is None else value
        self._initial_value, _ = _get_initial_value_and_type(value, self._initial_value_type)
        self.logger.debug("Updated initial value", initial_value=self._initial_value, previous_value=previous)
        for child in self._children.values():  # a complex variable: each child resets to its part of the new value
            child.initial_value = getattr(value, child._field_name)  # noqa: SLF001

    async def set_writable(self, writable: bool) -> None:
        """Set the variable to ``writable`` externally. Internally, a variable is always writable."""
        await self.ua_node.set_writable(writable)
        self._writable = writable
        self.logger.debug("Set variable writable", writable=writable)
        for child in self._children.values():  # a complex variable: the children always share its writability
            await child.set_writable(writable)

    async def set_historize(self, historize: bool, *, period: timedelta = timedelta(days=7), count: int = 1000) -> None:
        """Set the variable to ``historize``.

        :param historize: Whether the variable should be historized or not. The other parameters are ignored
            when ``False``.
        :param period: data older than this will be deleted from the history. By default, keep the last 7 days.
        :param count: max. number of value changes to keep in the history. ``0`` = no count limit
        """
        if historize:
            await self.server.ua_server.historize_node_data_change(self.ua_node, period=period, count=count)
        else:
            await self.server.ua_server.dehistorize_node_data_change(self.ua_node)
        self._historize = historize
        self.logger.debug("Set variable historize", historize=historize)

    async def _data_type(self) -> ua.NodeId | int | None:
        """Return the DataType of the variable's node; ``None`` lets asyncua derive it from the value.

        The most explicit choice wins: ``ua_data_type`` as given (its class must already be registered or bound with
        it), else the DataType the enum or dataclass already has (bound or created before), else one created now in
        namespace 1.
        """
        if self._ua_data_type is not None:
            return self._ua_data_type
        if issubclass(self._initial_value_type, IntEnum):
            return await self.server.ensure_enum(self._initial_value_type)  # pyright: ignore[reportArgumentType]
        if self._structure is not None:
            return await self.server.ensure_structure(self._structure)
        return None

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        raise NotImplementedError  # We don't use this since we override own _ua_create_node

    @override
    async def _ua_create_node(
        self, ua_location: Node, *, instantiate_optional: bool, exist_ok: bool, remove_existing: bool
    ) -> None:
        node_id = get_node_id(ua_location, name=self.name)
        browse_name = ua.QualifiedName(self.name, NamespaceIndex=1)
        typed = issubclass(self._initial_value_type, IntEnum) or self._structure is not None
        if typed and (self._unit is not None or self._range is not None):
            msg = "Enums and structures cannot be used with units or ranges!"
            raise ValueError(msg)

        try:
            if self._unit is None:
                if self._range is not None:  # notify programmer of their error instead of silently ignoring it
                    msg = "Ranges can only be used in combination with a unit!"
                    raise ValueError(msg)

                self.ua_node = await ua_location.add_variable(
                    nodeid=node_id, bname=browse_name, val=self._initial_value, datatype=await self._data_type()
                )
                if self._ua_value_rank is not None:
                    await self.ua_node.write_value_rank(self._ua_value_rank)
                return
            if self._range is None:
                object_type = ua.object_ids.ObjectIds.AnalogUnitType
            else:
                object_type = ua.object_ids.ObjectIds.AnalogUnitRangeType

            if self._ua_data_type is not None:
                msg = "custom ua_data_type specification is not supported for variables with units!"
                # Note: fixed 'Number' data type according to OPC UA specification OPC-10000-8/5.3.2
                raise ValueError(msg)

            self.ua_node = await ua_location.add_object(
                nodeid=node_id,
                bname=browse_name,
                objecttype=object_type,
                instantiate_optional=False,
            )
            if self._ua_value_rank is not None:
                await self.ua_node.write_value_rank(self._ua_value_rank)
        except ua.uaerrors.BadNodeIdExists:
            if exist_ok:
                self.logger.warning(
                    "Variable node already exists, skipping instantiation!",
                    node_id=node_id.to_string(),
                )
                self.ua_node = Node(ua_location.session, node_id)
                data_value = await self.ua_node.read_data_value()
                assert data_value.Value is not None
                self._initial_value = data_value.Value
                self._variant_type = await self.ua_node.read_data_type_as_variant_type()
                properties = await get_properties(self.ua_node)
                self._unit = properties.unit
                if properties.range is not None:
                    self._range = (properties.range.Low, properties.range.High)
            elif remove_existing:
                msg = "remove_existing parameter is not implemented yet!"
                raise NotImplementedError(msg) from None  # TODO(CaHa)
            else:
                raise

    async def add_reference(self, target: UaObject) -> None:
        """Add a reference to the given ``target`` `UaObject`, i.e. a `Resource`.

        Defines valid values for `UaObject` variable types.
        """
        if not issubclass(self._initial_value_type, UaObject):
            msg = f"Variable type of {self.path} is not of type UaObject: {self._initial_value_type}!"
            raise TypeError(msg)
        assert isinstance(target, UaObject)
        self._references.add(target)
        if self.is_initialized and target.is_initialized:
            # we can only add OPC UA references when both UaObjects have ua nodes
            await self._ua_add_reference(target)

    async def _ua_add_reference(self, target: UaObject) -> None:
        await self.ua_node.add_reference(
            target=target.ua_node,
            reftype=ua.FourByteNodeId(ua.Int32(ua.object_ids.ObjectIds.Utilizes)),
        )
        self.logger.debug("Added utilizes reference", target=target.path)

    async def remove_reference(self, target: UaObject) -> None:
        """Remove the existing reference to the given ``target`` `UaObject`."""
        assert isinstance(target, UaObject)
        self._references.remove(target)
        await self.ua_node.delete_reference(
            target=target.ua_node,
            reftype=ua.FourByteNodeId(ua.Int32(ua.object_ids.ObjectIds.Utilizes)),
        )
        self.logger.debug("Removed utilizes reference", target=target.path)

    async def write_check(self, value: _VariableType | ua.Variant | None) -> None:
        """Check whether given ``value`` can be written to this variable.

        :raises OutOfRangeError: if ``value`` is not within the range of the variable.
        """
        self.logger.debug("write check", value=value)

        if value is None and not self.optional_ok and self._initial_value_type is not str:
            msg = f"None is not a valid value for {self.path}!"
            raise OutOfRangeError(msg)

        if isinstance(value, ua.Variant):
            value = value.Value

        # before the callback, which may then rely on the class; None was decided above
        if self._structure is not None and value is not None:
            _check_structure_value(self._structure, value, array=self._structure_array)

        if self._write_check_callback is not None:
            if inspect.iscoroutinefunction(self._write_check_callback):
                ret = await self._write_check_callback(value)  # pyright: ignore[reportArgumentType]
            else:
                ret = self._write_check_callback(value)  # pyright: ignore[reportArgumentType]
            if ret is not None and ret is False:
                msg = "write_check_callback returned False."
                raise OutOfRangeError(msg)

        if isinstance(value, UaObject) and value not in self._references:
            msg = f"{value=} is not referenced as a valid value for {self.path}!"
            raise OutOfRangeError(msg)
        if isinstance(value, ua.NodeId):
            if value.is_null() and self.optional_ok:
                return

            try:
                obj = self.server.get_ua_object(value)
                if obj not in self._references:
                    msg = f"{value=} is a valid {obj}, but not referenced as a valid value for {self.path}!"
                    raise OutOfRangeError(msg)
            except KeyError:
                msg = f"{value=} is not a valid UaObject!"
                raise OutOfRangeError(msg) from None

        if not self.check_range(value):
            assert self._range is not None
            msg = (
                f"Value {value} is out of range for {self.path}! Must be between {self._range[0]} and {self._range[1]}!"
            )
            raise OutOfRangeError(msg)

        if issubclass(self._initial_value_type, IntEnum):
            try:
                value = self._initial_value_type(value)
            except ValueError as err:
                msg = f"{self.path}: Given value {value} is outside of valid values for {self._initial_value_type}!"
                raise OutOfRangeError(msg) from err

        await self._check_at_root(value)

    async def write(self, value: _VariableType | ua.Variant | None) -> None:
        """Write given ``value`` to this variable.

        **Note**: ``None`` is only valid for a small set of variable types:
        ``str`` and `UaObject` based (internally OPC UA nodes)

        :raises OutOfRangeError: if ``value`` is not within the range of the variable.
        """
        # self.write_check(value)  # is checked by access control
        if isinstance(value, UaObject):
            value = value.ua_node.nodeid  # pyright: ignore[reportAssignmentType]
        await write_value(node=self.ua_node, value=value, variant_type=self._initial_value.VariantType)
        # self.logger.debug("Written to variable", value=new_value)

    def _extract_value(self, value) -> _VariableType:
        if issubclass(self._initial_value_type, IntEnum):
            return self._initial_value_type(value)
        if issubclass(self._initial_value_type, UaObject):
            if isinstance(value, Node):
                value = value.nodeid
            assert isinstance(value, ua.NodeId), f"{value=} is not a valid NodeId!"
            if value.is_null():
                msg = f"No valid value provided for '{self.path}'!"
                raise NoValidValueReadError(msg)
            ua_object = self.server.get_ua_object(value)
            assert isinstance(ua_object, self._initial_value_type), (
                f"UaObject does not have type {self._initial_value_type}: {ua_object.__class__.__name__}!"
            )
            return ua_object  # pyright: ignore[reportReturnType]
        return value

    async def read(self) -> _VariableType:
        """Read current value from variable.

        :raises PyUaRuntimeError: If reading an `UaObject` based variable type would result in ``None`` due to
            Null Node ID.
        """
        value = await self.ua_node.read_value()
        return self._extract_value(value)

    async def read_optional(self) -> _VariableType | None:
        """Read current value from variable. Might be ``None``."""
        try:
            return await self.read()
        except NoValidValueReadError:
            return None

    async def reset(self) -> None:
        """Reset variable to initial value."""
        await self.write(self._initial_value)

    def check_range(self, value: _VariableType | ua.Variant | None) -> bool:
        """Check whether the given value is within the range.

        :returns: ``True`` if the given value is within the range (or there is no range specified), ``False`` otherwise.
        """
        if self._range is None:
            return True
        if isinstance(value, ua.Variant):
            value = value.Value
        assert isinstance(value, (float, int)), f"{self.path}: {value} is not a float or int!"
        low, high = self._range
        return low <= value <= high

    def clone(
        self,
        *,
        name: str,
        writable: bool | None = None,
        description: str | None = None,
    ) -> "UaVariable[_VariableType]":
        """Clone the variable configuration. It must be initialized before using it.

        :param name: The name of the variable.
        :param writable: Override ``writable`` configuration in clone if not ``None``.
        :param description: Override ``description`` in clone if not ``None``.
        """
        if writable is None:
            writable = self._writable
        return UaVariable(  # pyright: ignore[reportReturnType]
            name=name,
            minimum_access_level=self.minimum_access_level,
            initial_value=self._initial_value,
            unit=self._unit,
            range=self._range,
            writable=writable,
            historize=self._historize,
            bypass_lock=self.bypass_lock,
            variable_type=self._variable_type or self._initial_value_type,
            optional_ok=self.optional_ok,
            write_check_callback=self._write_check_callback,  # pyright: ignore[reportArgumentType]
            dictionary_entry=self._dictionary_entry,
            description=description or self.description,
            ua_value_rank=self._ua_value_rank,
            ua_data_type=self._ua_data_type,
            display_name=self._display_name,
        )

    @override
    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield "initial_value", self._initial_value
        yield "initial_value_type", self._initial_value_type
        yield "unit", self._unit
        yield "range", self._range
        yield "writable", self._writable
        yield "historize", self._historize
        yield "variant_type", self._variant_type
        yield "optional_ok", self.optional_ok
        yield from super()._repr_items()
