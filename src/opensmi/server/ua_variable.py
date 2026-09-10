# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Abstraction-layer for OPC UA variables our framework users interacts with."""

from collections.abc import AsyncGenerator, Iterator
from datetime import timedelta
from enum import IntEnum
from typing import Generic

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

from opensmi.server.mixins.parent_mixin import ParentMixin
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

    if initial_value is not None:
        return ua.Variant(Value=initial_value), type(initial_value)

    msg = f"Please provided either a non-Null {initial_value=} or a compatible {variable_type=}!"
    raise ValueError(msg)


class UaVariable(UaObject, ParentMixin[UaObject], Generic[_VariableType]):
    """OPC UA Variable for use in `ParameterSet`, `Monitoring`, `FinalResultData`, etc.

    Abstracts raw OPC UA/``asyncua`` away and provides a simple, high-level interface.
    """

    _initial_value_type: type[_VariableType]
    _initial_value: ua.Variant
    _unit: ua.EUInformation | None

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
            ``inital_value``, i.e. when ``None`` is provided.
        """
        super().__init__(minimum_access_level=minimum_access_level, bypass_lock=bypass_lock, **kwargs)  # pyright: ignore[reportArgumentType]

        self._initial_value, self._initial_value_type = _get_initial_value_and_type(initial_value, variable_type)
        assert self._initial_value.VariantType != ua.VariantType.Null

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

    @lifecycle
    async def _init(self) -> AsyncGenerator[None]:
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

        yield
        # no shutdown

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

    async def set_writable(self, writable: bool) -> None:
        """Set the variable to ``writable`` externally. Internally, a variable is always writable."""
        await self.ua_node.set_writable(writable)
        self._writable = writable
        self.logger.debug("Set variable writable", writable=writable)

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

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        raise NotImplementedError  # We don't use this since we override own _ua_create_node

    @override
    async def _ua_create_node(
        self, ua_location: Node, *, instantiate_optional: bool, exist_ok: bool, remove_existing: bool
    ) -> None:
        node_id = get_node_id(ua_location, name=self.name)
        browse_name = ua.QualifiedName(self.name, NamespaceIndex=1)

        try:
            if issubclass(self._initial_value_type, IntEnum):
                if self._unit is not None or self._range is not None:
                    msg = "Enums cannot be used with units or ranges!"
                    raise ValueError(msg)

                ua_datatype_node = await self.server.get_enum(self._initial_value_type)  # pyright: ignore[reportArgumentType]
                self.ua_node = await ua_location.add_variable(
                    nodeid=node_id, bname=browse_name, val=self._initial_value, datatype=ua_datatype_node
                )

            elif self._unit is None:
                if self._range is not None:  # notify programmer of their error instead of silently ignoring it
                    msg = "Ranges can only be used in combination with a unit!"
                    raise ValueError(msg)

                self.ua_node = await ua_location.add_variable(
                    nodeid=node_id, bname=browse_name, val=self._initial_value
                )
                return
            else:
                if self._range is None:
                    object_type = ua.object_ids.ObjectIds.AnalogUnitType
                else:
                    object_type = ua.object_ids.ObjectIds.AnalogUnitRangeType

                self.ua_node = await ua_location.add_object(
                    nodeid=node_id,
                    bname=browse_name,
                    objecttype=object_type,
                    instantiate_optional=False,
                )
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
            msg = f"Variable type of {self.full_name} is not of type UaObject: {self._initial_value_type}!"
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
        self.logger.debug("Added utilizes reference", target=target.full_name)

    async def remove_reference(self, target: UaObject) -> None:
        """Remove the existing reference to the given ``target`` `UaObject`."""
        assert isinstance(target, UaObject)
        self._references.remove(target)
        await self.ua_node.delete_reference(
            target=target.ua_node,
            reftype=ua.FourByteNodeId(ua.Int32(ua.object_ids.ObjectIds.Utilizes)),
        )
        self.logger.debug("Removed utilizes reference", target=target.full_name)

    def write_check(self, value: _VariableType | ua.Variant | None) -> None:
        """Check whether given ``value`` can be written to this variable.

        :raises OutOfRangeError: if ``value`` is not within the range of the variable.
        """
        self.logger.debug("write check", value=value)

        if value is None and not self.optional_ok and self._initial_value_type is not str:
            msg = f"None is not a valid value for {self.full_name}!"
            raise OutOfRangeError(msg)

        if isinstance(value, ua.Variant):
            value = value.Value

        if isinstance(value, UaObject) and value not in self._references:
            msg = f"{value=} is not referenced as a valid value for {self.full_name}!"
            raise OutOfRangeError(msg)
        if isinstance(value, ua.NodeId):
            if value.is_null() and self.optional_ok:
                return

            try:
                obj = self.server.get_ua_object(value)
                if obj not in self._references:
                    msg = f"{value=} is a valid {obj}, but not referenced as a valid value for {self.full_name}!"
                    raise OutOfRangeError(msg)
            except KeyError:
                msg = f"{value=} is not a valid UaObject!"
                raise OutOfRangeError(msg) from None

        if not self.check_range(value):
            assert self._range is not None
            msg = f"Value {value} is out of range for {self.full_name}! Must be between {self._range[0]} and {self._range[1]}!"
            raise OutOfRangeError(msg)

        if issubclass(self._initial_value_type, IntEnum):
            try:
                value = self._initial_value_type(value)
            except ValueError as err:
                msg = (
                    f"{self.full_name}: Given value {value} is outside of valid values for {self._initial_value_type}!"
                )
                raise OutOfRangeError(msg) from err

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
                msg = f"No valid value provided for '{self.full_name}'!"
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
        assert isinstance(value, (float, int)), f"{self.full_name}: {value} is not a float or int!"
        low, high = self._range
        return low <= value <= high

    def clone(self, *, name: str, writable: bool | None = None) -> "UaVariable[_VariableType]":
        """Clone the variable configuration. It must be initialized before using it.

        :param name: The name of the variable.
        :param writable: Override ``writable`` configuration in clone if not ``None``.
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
            variable_type=self._initial_value_type,
            optional_ok=self.optional_ok,
        )

    @override
    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield "initial_value", self._initial_value
        yield "initial_value_type", self._initial_value_type
        yield "unit", self._unit
        yield "range", self._range
        yield "writable", self._writable
        yield "historize", self._historize
        yield "variable_type", self._initial_value_type
        yield "optional_ok", self.optional_ok
        yield from super()._repr_items()
