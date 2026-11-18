# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from dataclasses import dataclass

import pytest
from asyncua import ua
from asyncua.client.client import Client
from asyncua.ua.object_ids import ObjectIds
from structlog.testing import capture_logs

from opensmi.server import BaseMachine, FinalResultData, Monitoring, ParameterSet, UaVariable, VariableGroup
from opensmi.server.mixins import FinalResultDataMixin, MonitoringMixin, ParameterSetMixin


class FollowPath(VariableGroup):
    MaxVelX = UaVariable(initial_value=0.5, bypass_lock=True)


class Controller(VariableGroup):
    UseSimTime = UaVariable(initial_value=False, bypass_lock=True)
    FollowPath = FollowPath()


class Parameters(ParameterSet):
    Controller = Controller()
    Plain = UaVariable(initial_value=1.0, bypass_lock=True)


class Readings(Monitoring):
    Controller = Controller()


class Results(FinalResultData):
    Controller = Controller()


class GroupMachine(
    MonitoringMixin[Readings], ParameterSetMixin[Parameters], FinalResultDataMixin[Results], BaseMachine
):
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("1")
        await self.identification.ProductInstanceUri.write("urn:example:1")
        await self.identification.Manufacturer.write(ua.LocalizedText("Example", "en"))


@pytest.fixture(params=["parameter_set", "monitoring", "final_result_data"])
def where(request) -> str:
    return request.param


def url(server) -> str:
    return f"opc.tcp://operator:operator@localhost:{server.ua_server.bserver.port}"


async def browse(node) -> dict[str, "object"]:
    return {(await child.read_browse_name()).Name: child for child in await node.get_children()}


@pytest.mark.asyncio
async def test_declared_groups_are_objects_of_base_object_type_nested_two_levels(empty_server, where) -> None:
    container = getattr(await empty_server.add_machine(GroupMachine()), where)

    controller = container.groups["Controller"]
    follow_path = controller.groups["FollowPath"]
    references = await container.ua_node.get_references(
        refs=ObjectIds.HasComponent, direction=ua.BrowseDirection.Forward
    )

    assert controller.ua_node.nodeid in {each.NodeId for each in references}
    assert await controller.ua_node.read_node_class() == ua.NodeClass.Object
    assert (await controller.ua_node.read_type_definition()) == ua.NodeId(ObjectIds.BaseObjectType)
    assert set(await browse(controller.ua_node)) == {"UseSimTime", "FollowPath"}
    assert set(await browse(follow_path.ua_node)) == {"MaxVelX"}
    assert follow_path["MaxVelX"].path.segments[-4:] == (container.name, "Controller", "FollowPath", "MaxVelX")
    assert (await controller.ua_node.read_display_name()).Text == "Controller"


@pytest.mark.asyncio
async def test_runtime_groups_nest_two_levels_in_each_variable_group(empty_server, where) -> None:
    container = getattr(await empty_server.add_machine(GroupMachine()), where)

    sensors = await container.add_group("Sensors", display_name="all sensors")
    left = await sensors.add_group("Left")
    level = UaVariable(initial_value=0.0, name="Level", bypass_lock=True)
    await left.add(level)

    assert isinstance(sensors, VariableGroup)
    assert (await sensors.ua_node.read_display_name()).Text == "all sensors"
    assert (await left.ua_node.read_display_name()).Text == "Left"
    assert (await level.ua_node.get_parent()).nodeid == left.ua_node.nodeid
    assert container.groups["Sensors"].groups["Left"]["Level"] is level
    assert (await empty_server.resolve(str(level.path)).read()) == 0.0


@pytest.mark.asyncio
async def test_a_runtime_keyed_name_may_have_spaces_and_colons(empty_server) -> None:
    monitoring = (await empty_server.add_machine(GroupMachine())).monitoring

    with capture_logs() as logs:
        member = await monitoring.add_group("sensor 1: left", runtime_keyed=True)
    await member.add(UaVariable(initial_value=0.0, name="Level"))

    assert (await member.ua_node.read_browse_name()).Name == "sensor 1: left"
    assert (await member.ua_node.read_display_name()).Text == "sensor 1: left"
    assert monitoring.groups["sensor 1: left"]["Level"].path.segments[-2] == "sensor 1: left"
    assert not [each for each in logs if "naming rule" in each["event"]]


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["", "a/b"])
async def test_a_runtime_keyed_name_is_still_held_to_the_hard_rule(empty_server, name) -> None:
    monitoring = (await empty_server.add_machine(GroupMachine())).monitoring

    with pytest.raises(ValueError, match=r"segment|empty|'/'"):
        await monitoring.add_group(name, runtime_keyed=True)

    assert name not in monitoring.groups


@pytest.mark.asyncio
async def test_a_duplicate_group_is_refused_and_the_first_is_kept(empty_server) -> None:
    monitoring = (await empty_server.add_machine(GroupMachine())).monitoring
    first = await monitoring.add_group("Sensors")

    with pytest.raises(RuntimeError):
        await monitoring.add_group("Sensors")

    assert monitoring.groups["Sensors"] is first


@pytest.mark.asyncio
async def test_a_group_name_that_breaks_the_naming_rule_is_warned_about_like_any_name(empty_server) -> None:
    monitoring = (await empty_server.add_machine(GroupMachine())).monitoring

    with capture_logs() as logs:
        group = await monitoring.add_group("not a name")

    assert [each for each in logs if "naming rule" in each["event"] and each["path"].endswith("not a name")]
    assert monitoring.groups["not a name"] is group


@pytest.mark.asyncio
async def test_remove_group_removes_the_whole_subtree_and_frees_the_name(empty_server) -> None:
    machine = await empty_server.add_machine(GroupMachine())
    parameters = machine.parameter_set
    sensors = await parameters.add_group("Sensors")
    left = await sensors.add_group("Left")
    level = UaVariable(initial_value=0.0, name="Level", bypass_lock=True)
    await left.add(level)
    level_path = str(level.path)

    await parameters.remove_group("Sensors")

    assert "Sensors" not in parameters.groups
    assert set(await browse(parameters.ua_node)) == {"Controller", "Plain"}
    with pytest.raises(ua.UaStatusCodeError):
        await level.ua_node.read_value()
    assert empty_server.resolve_optional(level_path) is None
    assert await parameters.add_group("Sensors") is not sensors
    with pytest.raises(KeyError):
        await parameters.remove_group("Nope")


@pytest.mark.asyncio
async def test_read_all_and_write_all_include_groups_as_nested_mappings(empty_server) -> None:
    parameters = (await empty_server.add_machine(GroupMachine())).parameter_set
    extra = await parameters.add_group("Extra")
    await extra.add(UaVariable(initial_value=3, name="Count", bypass_lock=True))

    assert await parameters.read_all() == {
        "Plain": 1.0,
        "Controller": {"UseSimTime": False, "FollowPath": {"MaxVelX": 0.5}},
        "Extra": {"Count": 3},
    }

    await parameters.write_all({"Plain": 2.0, "Controller": {"FollowPath": {"MaxVelX": 1.5}}, "Extra": {"Count": 4}})

    assert await parameters.groups["Controller"].groups["FollowPath"]["MaxVelX"].read() == 1.5
    assert await parameters.groups["Controller"]["UseSimTime"].read() is False
    assert await parameters.read_all() == {
        "Plain": 2.0,
        "Controller": {"UseSimTime": False, "FollowPath": {"MaxVelX": 1.5}},
        "Extra": {"Count": 4},
    }


@pytest.mark.asyncio
async def test_item_access_covers_variables_only_and_groups_have_their_own_mapping(empty_server) -> None:
    parameters = (await empty_server.add_machine(GroupMachine())).parameter_set
    extra = await parameters.add_group("Extra")

    assert parameters.groups == {"Controller": parameters.Controller, "Extra": extra}
    assert parameters.Controller.FollowPath is parameters.groups["Controller"].groups["FollowPath"]
    assert "Plain" in parameters
    assert "Controller" not in parameters
    assert [variable.name for variable in parameters] == ["Plain"]
    assert len(parameters) == 1
    with pytest.raises(KeyError):
        parameters["Controller"]
    assert "Nope" not in parameters
    with pytest.raises(KeyError):
        parameters["Nope"]


@pytest.mark.asyncio
async def test_reset_all_resets_variables_in_groups(empty_server) -> None:
    parameters = (await empty_server.add_machine(GroupMachine())).parameter_set
    await parameters.groups["Controller"].groups["FollowPath"]["MaxVelX"].write(9.0)

    await parameters.reset_all()

    assert await parameters.groups["Controller"].groups["FollowPath"]["MaxVelX"].read() == 0.5


@pytest.mark.asyncio
async def test_groups_inherit_writability_and_access_level_from_their_container(empty_server) -> None:
    machine = await empty_server.add_machine(GroupMachine())
    parameters, monitoring = machine.parameter_set, machine.monitoring
    assert parameters.groups["Controller"].groups["FollowPath"].minimum_access_level == parameters.minimum_access_level
    parameters.minimum_access_level = 3
    runtime = await parameters.add_group("Runtime")
    await runtime.add(UaVariable(initial_value=0.0, name="Value", bypass_lock=True, writable=False))

    assert runtime.minimum_access_level == 3
    assert (await runtime.add_group("Deeper")).minimum_access_level == 3
    assert parameters.groups["Controller"].groups["FollowPath"]["MaxVelX"]._writable is True
    assert monitoring.groups["Controller"].groups["FollowPath"]["MaxVelX"]._writable is False

    async with Client(url(empty_server)) as client:
        writable = client.get_node(parameters.groups["Controller"].groups["FollowPath"]["MaxVelX"].ua_node.nodeid)
        await writable.write_value(ua.Variant(2.5, ua.VariantType.Double))
        assert await parameters.groups["Controller"].groups["FollowPath"]["MaxVelX"].read() == 2.5

        read_only = client.get_node(monitoring.groups["Controller"].groups["FollowPath"]["MaxVelX"].ua_node.nodeid)
        with pytest.raises(ua.UaStatusCodeError):
            await read_only.write_value(ua.Variant(2.5, ua.VariantType.Double))


@dataclass
class Spot:
    x: float = 0.0
    y: float = 0.0


@pytest.mark.asyncio
async def test_a_complex_variable_works_inside_a_group(empty_server) -> None:
    parameters = (await empty_server.add_machine(GroupMachine())).parameter_set
    station = await parameters.add_group("Station")
    spot = UaVariable(initial_value=Spot(1.0, 2.0), name="Spot", bypass_lock=True, writable=True)
    await station.add(spot)

    await spot["Y"].write(5.0)

    assert await parameters.groups["Station"]["Spot"].read() == Spot(1.0, 5.0)

    child_path = str(spot["Y"].path)
    await parameters.remove_group("Station")
    assert empty_server.resolve_optional(child_path) is None


@pytest.mark.asyncio
async def test_remove_group_removes_a_declared_group_from_its_container(empty_server) -> None:
    parameters = (await empty_server.add_machine(GroupMachine())).parameter_set

    await parameters.remove_group("Controller")

    assert "Controller" not in parameters.groups
    assert "Controller" not in vars(parameters)
    assert set(await browse(parameters.ua_node)) == {"Plain"}


class Process(VariableGroup):
    Speed = UaVariable(initial_value=0.0, bypass_lock=True)


class MachineryReadings(Monitoring):
    Process = Process(object_type=ua.NodeId(ObjectIds.FolderType))


class MachineryMachine(MonitoringMixin[MachineryReadings], BaseMachine):
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("2")
        await self.identification.ProductInstanceUri.write("urn:example:2")
        await self.identification.Manufacturer.write(ua.LocalizedText("Example", "en"))


@pytest.mark.asyncio
async def test_a_group_can_be_a_folder_like_the_machinery_monitoring_sub_structures(empty_server) -> None:
    monitoring = (await empty_server.add_machine(MachineryMachine())).monitoring

    declared = monitoring.groups["Process"]
    added = await monitoring.add_group("Health", object_type=ua.NodeId(ObjectIds.FolderType))
    nested = await added.add_group("Detail")

    assert await declared.ua_node.read_type_definition() == ua.NodeId(ObjectIds.FolderType)
    assert await added.ua_node.read_type_definition() == ua.NodeId(ObjectIds.FolderType)
    assert await nested.ua_node.read_type_definition() == ua.NodeId(ObjectIds.BaseObjectType)
    assert set(await browse(declared.ua_node)) == {"Speed"}
