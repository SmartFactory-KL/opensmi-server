# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Container implementation for `UaVariable`."""

from collections.abc import AsyncGenerator, Iterator, Mapping
from types import UnionType
from typing import Any, Union, get_args, get_origin, get_type_hints

from asyncua import ua
from opensmi.core.errors import RequiredVariableMissingError
from opensmi.core.lifecycle_mixin import lifecycle
from opensmi.core.ua_node_util import get_properties
from typing_extensions import override

from opensmi.server.nodesets import (
    DiNodeIds,
    MachineryNodeIds,
    SmartFactoryMachineSetNodeIds,
    SmartFactorySkillSetNodeIds,
)
from opensmi.server.ua_object import UaObject, UaObjectDefinition
from opensmi.server.ua_variable import UaVariable


class UaVariableContainer(UaObject):
    """Base class for `UaVariable` containers. Also provides a Pythonic container interface."""

    def __init__(
        self,
        *,
        name: str,
        minimum_access_level: int | None = None,
        writable: bool = False,
        **kwargs,
    ) -> None:
        """*Cooperative* constructor.

        :param name: The name of the container.
        :param minimum_access_level: The minimum access level of the container.
        :param writable: Whether the all `UaVariable` of this container have external OPC UA write access.
        """
        super().__init__(
            name=name,
            minimum_access_level=minimum_access_level,
            **kwargs,
        )

        # class variables of type UaVariable are used as templates, we need to instantiate our own private ones.
        # find all variables, including inherited ones
        for cls in reversed(type(self).__mro__):  # type: ignore
            for name, class_variable in cls.__dict__.items():
                if isinstance(class_variable, UaVariable):
                    clone = class_variable.clone(name=name, writable=writable)
                    self.logger.debug(
                        "Cloned UaVariable template", name=name, class_variable=class_variable, cloned_variable=clone
                    )
                    setattr(self, name, clone)

    def _check_required_variables(self, annotations: dict[str, object]) -> None:
        """Check we have all required existing variables based on annotations."""
        for name, annotation in annotations.items():
            optional = False

            if get_origin(annotation) in (Union, UnionType):
                union_types = get_args(annotation)
                optional = type(None) in union_types

                # Remove None from the Union
                annotation = next(arg for arg in union_types if arg is not type(None))

            if get_origin(annotation) is not UaVariable:
                self.logger.debug("Skipping unsupported annotation", name=name, annotation=annotation)
                continue

            if not optional and not hasattr(self, name):
                msg = (
                    f"{self.__class__.__name__}: Could not find existing OPC UA variable for required "
                    f"UaVariable '{name}'!"
                )
                raise RequiredVariableMissingError(msg)

    async def _init_existing_variables(self) -> None:
        self.logger.debug("Initializing existing variables...")

        annotations: dict[str, object] = {}  # find all annotations, including inherited ones
        for cls in reversed(self.__class__.__mro__):  # type: ignore
            annotations.update(get_type_hints(cls))

        # create new UaVariable instances based on existing OPC UA variables
        for ua_variable_node in await self.ua_node.get_children(nodeclassmask=ua.NodeClass.Variable):
            browse_name = await ua_variable_node.read_browse_name()
            variant_type = await ua_variable_node.read_data_type_as_variant_type()
            value = await ua_variable_node.read_value()
            match variant_type:  # handle required special cases
                case ua.VariantType.LocalizedText:
                    value = ua.LocalizedText(value)
                case ua.VariantType.QualifiedName:
                    value = ua.QualifiedName(value)
            try:
                value = ua.uatypes.get_default_value(variant_type) if value is None else value
                initial_value = ua.Variant(Value=value, VariantType=variant_type)
            except ua.uaerrors.UaError as err:
                self.logger.exception(
                    "Cannot get initial value", browse_name=browse_name, variant_type=variant_type, value=value, err=err
                )
                raise

            properties = await get_properties(ua_variable_node, logger=self.logger)

            # check if there is a class var (template)
            variable_template = self.__class__.__dict__.get(str(browse_name.Name))
            if variable_template is not None:
                self.logger.debug("Found existing variable template, cloning!", browse_name=browse_name)
                assert isinstance(variable_template, UaVariable)
                assert not variable_template.is_initialized
                variable = variable_template.clone(name=str(browse_name.Name))
            else:
                if browse_name.Name not in annotations:
                    self.logger.warning("Found existing variable without type annotations!", browse_name=browse_name)
                variable = UaVariable(
                    name=str(browse_name.Name),  # type: ignore
                    initial_value=initial_value,
                    unit=properties.unit,
                    range=properties.range,
                )
            variable.parent = self
            variable.ua_node = ua_variable_node
            await variable.init()
            self.__setattr__(browse_name.Name, variable)

            self.logger.debug(
                "Added existing variable",
                browse_name=browse_name,
                inital_value=initial_value,
                variant_type=variant_type,
            )

        self._check_required_variables(annotations)

    @lifecycle
    async def _lifecycle_container(self) -> AsyncGenerator[None]:
        await self._init_existing_variables()

        # initialize our private UaVariable instances
        for variable in self:
            if not variable.is_initialized:
                variable.parent = self
                await variable.ua_create_node(self.ua_node)
                await variable.init()
        yield

    async def add(
        self,
        variable: UaVariable,
        *,
        exist_ok: bool = False,
        remove_existing: bool = False,
    ) -> None:
        """Add the given ``variable`` to the container. Can be used to add existing variables to other containers.

        If the variable is not initialized yet, it will be initialized with this container as parent.

        :param variable: UaVariable to add to the container.
        :param exist_ok: Whether to reuse an existing OPC UA variable node if found during initialization.
        :param remove_existing: Whether to remove existing OPC UA variable node if found during initialization.
        """
        setattr(self, variable.name, variable)
        if not variable.is_initialized:
            self.logger.debug("Initializing variable", variable=variable)
            variable.parent = self
            await variable.ua_create_node(self.ua_node, exist_ok=exist_ok, remove_existing=remove_existing)
            await variable.init()
        else:
            self.logger.debug("Referencing variable!", variable=variable)
            await self.ua_node.add_reference(variable.ua_node, reftype=ua.object_ids.ObjectIds.HasComponent)

    # @override
    # async def _after_init(self) -> None:
    #     await super()._after_init()
    #
    #     if len(self) == 0:
    #         self.logger.debug("Hiding OPC UA node", reason="Contains no variables.")
    #         await self._ua_hide_node()
    #
    # async def _ua_hide_node(self) -> None:
    #     """Hide our OPC UA node by deleting the reference to our parent."""
    #     await self.ua_node.delete_reference(
    #         target=self.parent.ua_node,
    #         reftype=ua.object_ids.ObjectIds.HasComponent,
    #         forward=False,
    #     )

    async def reset_all(self) -> None:
        """Reset all contained `UaVariable` to their initial value."""
        for variable in self:
            await variable.reset()

    async def read_all(self) -> Mapping[str, Any]:
        """Read all contained variables and return a mapping from variable name to value."""
        return {var.name: await var.read() for var in self}

    async def write_all(self, mapping: Mapping[str, Any]) -> None:
        """Write to all given ``mapping`` of variables name and their values.

        :raises KeyError: If no variable with given name in ``mapping`` exists.
        :raises OutOfRangeError: if value of ``mapping`` is not within the range of the corresponding variable.
        """
        for name, value in mapping.items():
            await self[name].write(value)

    #
    # Pythonic container interface
    #

    def __getitem__(self, name: str) -> UaVariable:
        """Get a `UaVariable` by the given ``name``.

        :raises KeyError: If no variable with given name exists.
        """
        if isinstance(variable := self.__dict__.get(name), UaVariable):
            return variable
        msg = f"No UaVariable named '{name}' contained in {self.path}!"
        raise KeyError(msg)

    def __contains__(self, key: object) -> bool:
        """Whether a variable with given ``name`` exists in this container."""
        return isinstance(key, str) and isinstance(self.__dict__.get(key), UaVariable)

    def __len__(self) -> int:
        """Return the number of contained `UaVariable`."""
        return sum(1 for var in self.__dict__.values() if isinstance(var, UaVariable))

    def __iter__(self) -> Iterator[UaVariable]:
        """Iterate over the contained `UaVariable`."""
        return (var for var in self.__dict__.values() if isinstance(var, UaVariable))


class ParameterSet(UaVariableContainer):
    """Base class for the `ParameterSet` container within Machine, Skill, Method, etc."""

    def __init__(self, *, minimum_access_level: int | None = None, **kwargs) -> None:
        """*Cooperative* constructor."""
        super().__init__(name="ParameterSet", minimum_access_level=minimum_access_level, writable=True, **kwargs)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=DiNodeIds.FunctionalGroupType,
            namespace_uri=SmartFactoryMachineSetNodeIds,
        )


class Monitoring(UaVariableContainer):
    """Base class for the `Monitoring` container within Machine, Skill, Method, etc."""

    def __init__(self, **kwargs) -> None:
        """*Cooperative* constructor."""
        super().__init__(name="Monitoring", minimum_access_level=None, writable=False, **kwargs)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=SmartFactoryMachineSetNodeIds.MonitoringType,
            namespace_uri=SmartFactoryMachineSetNodeIds,
        )


class Attributes(UaVariableContainer):
    """Base class for the `Attributes` container within Machine, Skill, Method, etc."""

    def __init__(self, **kwargs) -> None:
        """*Cooperative* constructor."""
        super().__init__(name="Attributes", minimum_access_level=None, writable=False, **kwargs)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=DiNodeIds.FunctionalGroupType,
            namespace_uri=SmartFactoryMachineSetNodeIds,
        )


class FinalResultData(UaVariableContainer):
    """Base class for the final result data container within skills (`BaseSkill`) and methods (`BaseMethod`)."""

    def __init__(self, *, minimum_access_level: int | None = None, **kwargs) -> None:
        """*Cooperative* constructor."""
        super().__init__(name="FinalResultData", minimum_access_level=minimum_access_level, writable=False, **kwargs)

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=DiNodeIds.FunctionalGroupType,
            namespace_uri=SmartFactorySkillSetNodeIds,
        )


class Identification(UaVariableContainer):
    """Abstract base class for the identification container.

    See OPC UA Machinery companion specification: abstract type ``MachineryItemIdentificationType``.
    """

    def __init__(self, *, minimum_access_level: int | None = None, **kwargs) -> None:
        """*Cooperative* constructor."""
        super().__init__(name="Identification", minimum_access_level=minimum_access_level, writable=False, **kwargs)


class MachineryComponentIdentification(Identification):
    """Base class for the identification container for machine and machinery items.

    See OPC UA Machinery companion specification: ``MachineryComponentIdentificationType``.
    """

    AssetId: UaVariable[str] | None
    ComponentName: UaVariable[ua.LocalizedText] | None
    Manufacturer: UaVariable[ua.LocalizedText]
    SerialNumber: UaVariable[str]

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=MachineryNodeIds.MachineryComponentIdentificationType,
            namespace_uri=MachineryNodeIds,
            reference_type=ua.object_ids.ObjectIds.HasAddIn,
        )


class MachineIdentification(MachineryComponentIdentification):
    """Identification container for machines (`BaseMachine`).

    OPC UA property ``ProductInstanceUri`` is mandatory, and it uses another OPC UA type: ``MachineIdentificationType``.
    """

    ProductInstanceUri: UaVariable[str]

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=MachineryNodeIds.MachineIdentificationType,
            namespace_uri=DiNodeIds,
            reference_type=ua.object_ids.ObjectIds.HasAddIn,
        )


async def init_container(*, parent: object, container: UaVariableContainer, exist_ok: bool = True) -> None:
    """Initialize the given ``container`` container with ``parent``."""
    assert isinstance(parent, UaObject)
    assert container is not None

    container.parent = parent
    await container.ua_create_node(parent.ua_node, exist_ok=exist_ok)
    await container.init()
