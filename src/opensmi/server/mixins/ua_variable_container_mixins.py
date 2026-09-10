# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Generic mixins for providing an `UaObject` with `Attributes`, `Monitoring`, `ParameterSet` and `FinalResultData`."""

from abc import abstractmethod
from collections.abc import AsyncGenerator, Mapping
from typing import Any, Generic, cast

import structlog
from asyncua import ua
from opensmi.core.errors import ValidationError
from opensmi.core.lifecycle_mixin import lifecycle
from opensmi.core.meta import resolve_generic_arguments
from opensmi.core.mixin_helpers import get_logger
from typing_extensions import TypeVar, deprecated

from opensmi.server.ua_variable import UaVariable
from opensmi.server.ua_variable_containers import (
    Attributes,
    FinalResultData,
    Identification,
    MachineryComponentIdentification,
    Monitoring,
    ParameterSet,
    init_container,
)

_LOGGER = structlog.get_logger("open_smi." + __name__)

AttributesType = TypeVar("AttributesType", bound="Attributes")
"""Attributes type for the `UaObject`. Defaults to base `Attributes` type."""
MonitoringType = TypeVar("MonitoringType", bound="Monitoring")
"""Monitoring type for the `UaObject`. Defaults to base `Monitoring` type."""
ParameterSetType = TypeVar("ParameterSetType", bound="ParameterSet")
"""ParameterSet type for the `UaObject`. Defaults to base `ParameterSet` type."""
FinalResultDataType = TypeVar("FinalResultDataType", bound="FinalResultData")
"""FinalResultData type for the `UaObject`. Defaults to base `FinalResultData` type."""
IdentificationType = TypeVar("IdentificationType", bound="Identification")
"""Identification type for the `UaObject`. Defaults to base `Identification` type."""


def _type_or_default(cls: type, *, default: type) -> type:
    """Return type checked type of ``cls`` or ``default`` if cls is ``NoneType``.

    :raises TypeError: if ``cls`` is no subclass of ``default``.
    """
    type_ = default if cls is type(None) else cls
    if not issubclass(type_, default):
        msg = f"{type_.__name__} must inherit from {default.__name__}"
        raise TypeError(msg)
    return type_


class AttributesMixin(Generic[AttributesType]):
    """Mixin for `UaObject` with attributes."""

    __attributes: AttributesType | None = None

    @lifecycle
    async def lifecycle_attributes(self) -> AsyncGenerator[None]:
        """Initialize the attributes `UaVariableContainer`."""
        await init_container(parent=self, container=self.attributes, exist_ok=True)
        yield
        # no shutdown

    @property
    def attributes(self) -> AttributesType:
        """Return the attributes `UaVariableContainer`. Read-only property."""
        if self.__attributes is None:
            type_ = getattr(self, "_attributes_type", Attributes)
            self.__attributes = cast(AttributesType, type_())
            get_logger(self, _LOGGER).debug("Instantiated Container", type=type_)
        return self.__attributes

    def __init_subclass__(cls, **kwargs) -> None:
        """Extract type for attributes."""
        super().__init_subclass__(**kwargs)

        result: tuple[type, ...] | None = resolve_generic_arguments(cls, AttributesMixin)
        if result is None or any(isinstance(x, TypeVar) for x in result):  # skip still-generic classes
            return

        cls._attributes_type = _type_or_default(result[0], default=Attributes)


class MonitoringMixin(Generic[MonitoringType]):
    """Mixin for `UaObject` with monitoring."""

    __monitoring: MonitoringType | None = None

    @lifecycle
    async def lifecycle_monitoring(self) -> AsyncGenerator[None]:
        """Initialize the monitoring `UaVariableContainer`."""
        await init_container(parent=self, container=self.monitoring, exist_ok=True)
        yield
        # no shutdown

    @property
    def monitoring(self) -> MonitoringType:
        """Return the monitoring `UaVariableContainer` . Read-only property."""
        if self.__monitoring is None:
            type_ = getattr(self, "_monitoring_type", Monitoring)
            self.__monitoring = cast(MonitoringType, type_())
            get_logger(self, _LOGGER).debug("Instantiated Container", type=type_)
        return self.__monitoring

    @deprecated("Please use the typed .monitoring.<name>.read() or untyped monitoring.read_all() instead")
    async def read_monitoring(self) -> Mapping[str, Any]:
        """Read all monitoring values and returns them as a dictionary.

        Keys are the display name of OPC UA nodes, values the corresponding values.
        """
        return await self.monitoring.read_all()

    def __init_subclass__(cls, **kwargs) -> None:
        """Extract type for monitoring."""
        super().__init_subclass__(**kwargs)

        result: tuple[type, ...] | None = resolve_generic_arguments(cls, MonitoringMixin)
        if result is None or any(isinstance(x, TypeVar) for x in result):  # skip still-generic classes
            return

        cls._monitoring_type = _type_or_default(result[0], default=Monitoring)


class ParameterSetMixin(Generic[ParameterSetType]):
    """Mixin for `UaObject` with parameters."""

    __parameter_set: ParameterSetType | None = None

    @lifecycle
    async def lifecycle_parameter_set(self) -> AsyncGenerator[None]:
        """Initialize the monitoring `UaVariableContainer`."""
        await init_container(parent=self, container=self.parameter_set, exist_ok=True)
        yield
        # no shutdown

    @property
    def parameter_set(self) -> ParameterSetType:
        """Return the parameter set `UaVariableContainer` . Read-only property."""
        if self.__parameter_set is None:
            type_ = getattr(self, "_parameter_set_type", ParameterSet)
            self.__parameter_set = cast(ParameterSetType, type_())
            get_logger(self, _LOGGER).debug("Instantiated Container", type=type_)
        return self.__parameter_set

    @deprecated("Please use the typed .parameter_set.<name>.read() instead")
    async def read_parameter(self, name: str) -> Any:
        """Read the value of the parameter with the given name."""
        return await self.parameter_set[name].read()

    @deprecated("Please use the typed .parameter_set.<name>.read() or untyped parameter_set.read_all() instead")
    async def read_parameters(self) -> Mapping[str, Any]:
        """Read all parameters and return them as a dictionary.

        Keys are the display name of OPC UA nodes, values the corresponding values.
        """
        return await self.parameter_set.read_all()

    @deprecated(
        "Please use the typed .parameter_set.<name>.write(<value>) instead or untyped parameter_set.write_all()"
    )
    async def write_parameters(self, parameters: Mapping[str, Any]) -> None:
        """Write the parameters of according to given dictionary.

        Keys of the dictionary are matched to the browse name of the OPC UA node.
        Values of the dictionary are written to the matched OPC UA node.
        """
        await self.parameter_set.write_all(parameters)

    @deprecated("Please use the typed .parameter_set.<name>.write(<value>) instead")
    async def write_parameter(self, name: str, value: Any) -> None:
        """Write given value to parameter with given name."""
        await self.parameter_set[name].write(value)

    def __init_subclass__(cls, **kwargs) -> None:
        """Extract type for monitoring."""
        super().__init_subclass__(**kwargs)

        result: tuple[type, ...] | None = resolve_generic_arguments(cls, ParameterSetMixin)
        if result is None or any(isinstance(x, TypeVar) for x in result):  # skip still-generic classes
            return

        cls._parameter_set_type: type = _type_or_default(result[0], default=ParameterSet)


class FinalResultDataMixin(Generic[FinalResultDataType]):
    """Mixin for `UaObject` with final result data."""

    __final_result_data: FinalResultDataType | None = None

    @lifecycle
    async def lifecycle_final_result_data(self) -> AsyncGenerator[None]:
        """Initialize the final result data `UaVariableContainer`."""
        await init_container(parent=self, container=self.final_result_data, exist_ok=True)
        yield
        # no shutdown

    @property
    def final_result_data(self) -> FinalResultDataType:
        """Return the final result data `UaVariableContainer` . Read-only property."""
        if self.__final_result_data is None:
            type_ = getattr(self, "_final_result_data_type", FinalResultData)
            self.__final_result_data = cast(FinalResultDataType, type_())
            get_logger(self, _LOGGER).debug("Instantiated Container", type=type_)
        return self.__final_result_data

    @deprecated("Please use the typed .final_result_data.<name>.read() instead")
    async def read_result(self, name: str) -> Any:
        """Read the result of given variable name."""
        return await self.final_result_data[name].read_optional()

    @deprecated("Please use the typed .final_result_data.<name>.read() or untyped final_result_data.read_all() instead")
    async def read_results(self) -> Mapping[str, Any]:
        """Read all results and provide them as a mapping from result name to value."""
        return await self.final_result_data.read_all()

    def __init_subclass__(cls, **kwargs) -> None:
        """Extract type for monitoring."""
        super().__init_subclass__(**kwargs)

        result: tuple[type, ...] | None = resolve_generic_arguments(cls, FinalResultDataMixin)
        if result is None or any(isinstance(x, TypeVar) for x in result):  # skip still-generic classes
            return

        cls._final_result_data_type = _type_or_default(result[0], default=FinalResultData)


class IdentificationMixin(Generic[IdentificationType]):
    """Mixin for `UaObject` with final result data."""

    __identification: IdentificationType | None = None

    @abstractmethod
    async def _write_identification(self) -> None:
        pass
        # TODO(CaHa): Decide on API before v5. either this hook or via config...

    @lifecycle
    async def lifecycle_identification(self) -> AsyncGenerator[None]:
        """Initialize the identification `UaVariableContainer`."""
        await init_container(parent=self, container=self.identification, exist_ok=True)

        # setup optional, client writable variables
        for variable_name in ["AssetId", "ComponentName"]:
            variable = getattr(self.identification, variable_name, None)
            if variable is None:  # optional variables are allowed to be not exist
                continue
            assert isinstance(variable, UaVariable), f"'{variable_name}' of {self} is not a UaVariable!"
            await variable.set_writable(True)

        await self._write_identification()
        yield
        # no shutdown

    @lifecycle(after=lifecycle_identification)
    async def validate_identification(self) -> AsyncGenerator[None]:
        """Check if mandatory identification variables have been set."""
        for variable_name in ["SerialNumber", "ProductInstanceUri", "Manufacturer"]:
            variable = getattr(self.identification, variable_name, None)
            if variable is None:  # optional variables are allowed to be not exist
                continue
            # but if they exist, they should be valid, non-empty variables
            assert isinstance(variable, UaVariable), f"'{variable_name}' of {self} is not a UaVariable!"
            value = await variable.read()
            if isinstance(value, ua.LocalizedText):
                value = value.Text
            if value is None or value == "":
                msg = f"{variable.full_name} must not be empty!"
                raise ValidationError(msg)

        yield
        # no shutdown

    @property
    def identification(self) -> IdentificationType:
        """Return the identification `UaVariableContainer` . Read-only property."""
        if self.__identification is None:
            type_ = getattr(self, "_identification_type", MachineryComponentIdentification)
            self.__identification = cast(IdentificationType, type_())
            get_logger(self, _LOGGER).debug("Instantiated Container", type=type_)
        return self.__identification

    def __init_subclass__(cls, **kwargs) -> None:
        """Extract type for identification."""
        super().__init_subclass__(**kwargs)

        result: tuple[type, ...] | None = resolve_generic_arguments(cls, IdentificationMixin)
        if result is None or any(isinstance(x, TypeVar) for x in result):  # skip still-generic classes
            return

        cls._identification_type = _type_or_default(result[0], default=Identification)
