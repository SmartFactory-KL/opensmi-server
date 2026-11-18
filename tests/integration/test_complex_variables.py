# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import sys
import textwrap
from dataclasses import dataclass, field, make_dataclass

import pytest
from asyncua import ua
from asyncua.client.client import Client
from asyncua.ua.object_ids import ObjectIds

from opensmi.server import BaseMachine, ParameterSet, UaVariable
from opensmi.server.mixins import ParameterSetMixin


@dataclass
class Edge:
    radius: float = 0.0
    chamfer: bool = False


@dataclass
class Size:
    length: float = 0.0
    width: float = 0.0
    edge: Edge = field(default_factory=Edge)


@dataclass
class Sample:
    name: str = ""
    value: float = 0.0


@dataclass
class Part:
    id: str = ""
    size: Size = field(default_factory=Size)
    in_tolerance: bool = True
    pose2d: float = 0.0
    samples: list[Sample] = field(default_factory=list)
    weights: list[float] = field(default_factory=list)


def make_part(**changes) -> Part:
    part = Part("P-1", Size(120.0, 80.0, Edge(1.5, True)), True, 0.25, [Sample("a", 1.0)], [0.5, 1.5])
    for name, value in changes.items():
        setattr(part, name, value)
    return part


SEEN: list[Part] = []


def guard(value: Part) -> bool:
    SEEN.append(value)
    return value.size.length <= 500.0


class Parameters(ParameterSet):
    Guarded = UaVariable(initial_value=make_part(), writable=True, bypass_lock=True, write_check_callback=guard)
    Current = UaVariable(initial_value=make_part(), writable=True, bypass_lock=True)
    Fixed = UaVariable(initial_value=make_part(), bypass_lock=True, minimum_access_level=3)
    Batch = UaVariable(initial_value=[make_part()], writable=True, bypass_lock=True)
    Count = UaVariable(initial_value=3, writable=True, bypass_lock=True, display_name="count of parts")


class PartMachine(ParameterSetMixin[Parameters], BaseMachine):
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("1")
        await self.identification.ProductInstanceUri.write("urn:example:1")
        await self.identification.Manufacturer.write(ua.LocalizedText("Example", "en"))


def url(server) -> str:
    return f"opc.tcp://operator:operator@localhost:{server.ua_server.bserver.port}"


async def browse(node) -> dict[str, "object"]:
    return {(await child.read_browse_name()).Name: child for child in await node.get_children()}


@pytest.mark.asyncio
async def test_a_dataclass_value_gets_one_child_per_field_named_in_pascal_case(empty_server) -> None:
    machine = await empty_server.add_machine(PartMachine())
    current = machine.parameter_set.Current

    children = await browse(current.ua_node)

    assert set(children) == {"Id", "Size", "InTolerance", "Pose2d", "Samples", "Weights"}
    assert list(current.children) == ["Id", "Size", "InTolerance", "Pose2d", "Samples", "Weights"]
    assert (await children["InTolerance"].read_display_name()).Text == "in_tolerance"
    assert (await children["Pose2d"].read_display_name()).Text == "pose2d"
    assert (await current.ua_node.read_display_name()).Text == "Current"


@pytest.mark.asyncio
async def test_children_are_components_with_the_data_types_of_the_fields(empty_server) -> None:
    machine = await empty_server.add_machine(PartMachine())
    current = machine.parameter_set.Current

    references = await current.ua_node.get_references(refs=ObjectIds.HasComponent, direction=ua.BrowseDirection.Forward)
    children = await browse(current.ua_node)

    assert {each.NodeId for each in references} == {each.nodeid for each in children.values()}
    assert await children["Id"].read_data_type() == ua.NodeId(ObjectIds.String)
    assert await children["InTolerance"].read_data_type() == ua.NodeId(ObjectIds.Boolean)
    assert await children["Size"].read_data_type() == ua.NodeId("Size", 1)
    assert await current.ua_node.read_data_type() == ua.NodeId("Part", 1)
    assert await children["Weights"].read_data_type() == ua.NodeId(ObjectIds.Double)
    assert await children["Weights"].read_value_rank() == ua.ValueRank.OneDimension


@pytest.mark.asyncio
async def test_nested_dataclasses_are_complex_again(empty_server) -> None:
    machine = await empty_server.add_machine(PartMachine())
    size = machine.parameter_set.Current["Size"]

    children = await browse(size.ua_node)
    edge_children = await browse(children["Edge"])

    assert set(children) == {"Length", "Width", "Edge"}
    assert set(edge_children) == {"Radius", "Chamfer"}
    assert await children["Length"].read_data_type() == ua.NodeId(ObjectIds.Double)
    assert await children["Edge"].read_data_type() == ua.NodeId("Edge", 1)
    assert await edge_children["Radius"].read_value() == 1.5
    assert size.path == machine.parameter_set.Current.path / "Size"
    assert machine.parameter_set.Current["Size"]["Edge"]["Radius"].path.segments[-4:] == (
        "Current",
        "Size",
        "Edge",
        "Radius",
    )


@pytest.mark.asyncio
async def test_children_read_like_any_variable(empty_server) -> None:
    machine = await empty_server.add_machine(PartMachine())
    current = machine.parameter_set.Current

    assert await current["Id"].read() == "P-1"
    assert await current["Size"]["Length"].read() == 120.0
    assert await current["Size"].read() == Size(120.0, 80.0, Edge(1.5, True))
    assert await current["Weights"].read() == [0.5, 1.5]
    with pytest.raises(KeyError):
        current["Nope"]


@pytest.mark.asyncio
async def test_a_list_of_structures_stays_one_leaf(empty_server) -> None:
    machine = await empty_server.add_machine(PartMachine())
    batch, current = machine.parameter_set.Batch, machine.parameter_set.Current

    assert batch.children == {}
    assert len(await browse(batch.ua_node)) == 0
    assert current["Samples"].children == {}
    assert await current["Samples"].read() == [Sample("a", 1.0)]
    assert await current["Samples"].ua_node.read_data_type() == ua.NodeId("Sample", 1)


@pytest.mark.asyncio
async def test_a_plain_variable_has_no_children_and_a_display_name_on_request(empty_server) -> None:
    machine = await empty_server.add_machine(PartMachine())
    count = machine.parameter_set.Count

    assert count.children == {}
    assert (await count.ua_node.read_display_name()).Text == "count of parts"
    assert (await count.ua_node.read_browse_name()).Name == "Count"
    assert (await machine.parameter_set.Current.ua_node.read_display_name()).Text == "Current"


@dataclass
class Gauge:
    reading: float = 0.0


@pytest.mark.asyncio
async def test_an_explicit_data_type_in_another_namespace_still_gets_children(empty_server) -> None:
    namespace = await empty_server.ua_server.register_namespace("urn:example:other")
    data_type = await empty_server.ensure_structure(Gauge, namespace_index=namespace)
    machine = await empty_server.add_machine(PartMachine())
    variable = UaVariable(Gauge(2.0), name="Gauge", ua_data_type=data_type, writable=True, bypass_lock=True)
    await machine.parameter_set.add(variable)

    assert await variable.ua_node.read_data_type() == data_type
    assert list(variable.children) == ["Reading"]
    assert await variable["Reading"].read() == 2.0


@pytest.mark.asyncio
async def test_a_class_that_makes_its_own_fields_gets_children_matching_them(empty_server) -> None:
    Made = make_dataclass("Made", [("first_one", float, 1.0), ("second", str, "x")])  # noqa: N806
    machine = await empty_server.add_machine(PartMachine())
    variable = UaVariable(Made(), name="Made", writable=True, bypass_lock=True)
    await machine.parameter_set.add(variable)

    assert list(variable.children) == ["FirstOne", "Second"]


CLIENT = textwrap.dedent("""
    import asyncio, json, logging, sys
    from asyncua.client.client import Client
    
    logging.disable(logging.CRITICAL)

    async def main(url, nodeid):
        async with Client(url) as client:
            await client.load_data_type_definitions()
            node = client.get_node(nodeid)
            length = await (await node.get_child(["1:Size", "1:Length"])).read_value()
            await (await node.get_child(["1:Size", "1:Length"])).write_value(99.0)
            whole = await node.read_value()
            print(json.dumps({"length": length, "after": whole.size.length, "id": whole.id}))

    asyncio.run(main(sys.argv[1], sys.argv[2]))
""")


@pytest.mark.asyncio
async def test_a_client_that_knows_no_class_reads_and_writes_a_leaf(empty_server) -> None:
    machine = await empty_server.add_machine(PartMachine())
    current = machine.parameter_set.Current

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        CLIENT,
        url(empty_server),
        current.ua_node.nodeid.to_string(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(process.communicate(), timeout=60)

    assert process.returncode == 0, err.decode()
    assert json.loads(out) == {"length": 120.0, "after": 99.0, "id": "P-1"}
    assert (await current.read()).size.length == 99.0


async def snapshot(variable: UaVariable) -> dict[str, tuple[object, object]]:
    """The value and SourceTimestamp of a variable and all variables below it."""
    data_value = await variable.ua_node.read_data_value()
    found = {str(variable.path): (data_value.Value.Value, data_value.SourceTimestamp)}
    for child in variable.children.values():
        found.update(await snapshot(child))
    return found


def timestamps(found: dict[str, tuple[object, object]]) -> set[object]:
    return {each[1] for each in found.values()}


@pytest.mark.asyncio
async def test_a_parent_write_updates_every_leaf(empty_server) -> None:
    current = (await empty_server.add_machine(PartMachine())).parameter_set.Current

    await current.write(Part("P-2", Size(10.0, 5.0, Edge(0.5, False)), False, 1.0, [], [2.0]))

    assert await current["Id"].read() == "P-2"
    assert await current["Size"]["Length"].read() == 10.0
    assert await current["Size"]["Edge"]["Chamfer"].read() is False
    assert await current["InTolerance"].read() is False
    assert await current["Samples"].read() == []
    assert await current["Weights"].read() == [2.0]
    assert await current["Size"].read() == Size(10.0, 5.0, Edge(0.5, False))


@pytest.mark.asyncio
async def test_a_leaf_write_updates_the_parent_and_the_complex_children_on_the_way_up(empty_server) -> None:
    current = (await empty_server.add_machine(PartMachine())).parameter_set.Current

    await current["Size"]["Edge"]["Radius"].write(9.0)

    assert await current["Size"]["Edge"].read() == Edge(9.0, True)
    assert await current["Size"].read() == Size(120.0, 80.0, Edge(9.0, True))
    assert await current.read() == make_part(size=Size(120.0, 80.0, Edge(9.0, True)))


@pytest.mark.asyncio
async def test_a_write_to_a_complex_child_updates_its_leaves_and_the_parent(empty_server) -> None:
    current = (await empty_server.add_machine(PartMachine())).parameter_set.Current

    await current["Size"].write(Size(1.0, 2.0, Edge(3.0, False)))

    assert await current["Size"]["Width"].read() == 2.0
    assert await current["Size"]["Edge"]["Radius"].read() == 3.0
    assert (await current.read()).size == Size(1.0, 2.0, Edge(3.0, False))
    assert (await current.read()).id == "P-1"


@pytest.mark.asyncio
async def test_external_writes_sync_in_both_directions(empty_server) -> None:
    current = (await empty_server.add_machine(PartMachine())).parameter_set.Current

    async with Client(url(empty_server)) as client:
        node = client.get_node(current.ua_node.nodeid)
        length = client.get_node(current["Size"]["Length"].ua_node.nodeid)
        await length.write_value(ua.Variant(7.0, ua.VariantType.Double))
        assert (await node.read_value()).size.length == 7.0

        await node.write_value(ua.Variant(make_part(id="P-3"), ua.VariantType.ExtensionObject))
        assert await length.read_value() == 120.0
        assert await client.get_node(current["Id"].ua_node.nodeid).read_value() == "P-3"


@pytest.mark.asyncio
async def test_a_write_gives_the_parent_and_the_affected_nodes_one_source_timestamp(empty_server) -> None:
    current = (await empty_server.add_machine(PartMachine())).parameter_set.Current
    before = await snapshot(current)

    await current["Size"]["Edge"]["Radius"].write(9.0)
    after = await snapshot(current)
    changed = {path for path in after if after[path][1] != before[path][1]}

    assert changed == {
        str(each.path)
        for each in (current, current["Size"], current["Size"]["Edge"], current["Size"]["Edge"]["Radius"])
    }
    assert len(timestamps({path: after[path] for path in changed})) == 1

    await current.write(make_part(id="P-9"))
    assert len(timestamps(await snapshot(current))) == 1


@pytest.mark.asyncio
async def test_the_status_of_a_parent_write_reaches_every_node(empty_server) -> None:
    current = (await empty_server.add_machine(PartMachine())).parameter_set.Current
    nodes = (current, current["Size"], current["Size"]["Edge"]["Radius"], current["Id"])

    for status in (ua.StatusCodes.BadNoCommunication, ua.StatusCodes.Good):
        value = ua.Variant(make_part(id="P-4"), ua.VariantType.ExtensionObject)
        await current.ua_node.write_value(ua.DataValue(Value=value, StatusCode=ua.StatusCode(status)))

        for node in nodes:
            data_value = await node.ua_node.read_data_value(raise_on_bad_status=False)
            assert data_value.StatusCode.value == status, node.path
    assert await current["Id"].read() == "P-4"


@pytest.mark.asyncio
async def test_a_refused_parent_write_changes_no_node(empty_server) -> None:
    guarded = (await empty_server.add_machine(PartMachine())).parameter_set.Guarded
    before = await snapshot(guarded)

    with pytest.raises(ua.UaStatusCodeError):
        await guarded.write(make_part(id="too long", size=Size(600.0)))

    assert await snapshot(guarded) == before


@pytest.mark.asyncio
async def test_a_refused_leaf_write_changes_no_node(empty_server) -> None:
    guarded = (await empty_server.add_machine(PartMachine())).parameter_set.Guarded
    before = await snapshot(guarded)

    with pytest.raises(ua.UaStatusCodeError):
        await guarded["Size"]["Length"].write(600.0)

    assert await snapshot(guarded) == before


@pytest.mark.asyncio
async def test_the_parents_callback_sees_the_merged_value_on_a_leaf_write(empty_server) -> None:
    guarded = (await empty_server.add_machine(PartMachine())).parameter_set.Guarded
    SEEN.clear()

    await guarded["Size"]["Length"].write(400.0)

    assert SEEN[-1] == make_part(size=Size(400.0, 80.0, Edge(1.5, True)))


@pytest.mark.asyncio
async def test_an_external_leaf_write_is_checked_by_the_parents_callback(empty_server) -> None:
    guarded = (await empty_server.add_machine(PartMachine())).parameter_set.Guarded
    before = await snapshot(guarded)

    async with Client(url(empty_server)) as client:
        length = client.get_node(guarded["Size"]["Length"].ua_node.nodeid)
        with pytest.raises(ua.UaStatusCodeError):
            await length.write_value(ua.Variant(600.0, ua.VariantType.Double))

    assert await snapshot(guarded) == before


@pytest.mark.asyncio
async def test_an_external_write_of_another_type_is_refused_with_its_status(empty_server) -> None:
    current = (await empty_server.add_machine(PartMachine())).parameter_set.Current
    before = await snapshot(current)

    async with Client(url(empty_server)) as client:
        node = client.get_node(current.ua_node.nodeid)
        with pytest.raises(ua.UaStatusCodeError) as refused:
            await node.write_value(ua.Variant(600.0, ua.VariantType.Double))

    assert refused.value.code == ua.StatusCodes.BadTypeMismatch
    assert await snapshot(current) == before


@pytest.mark.asyncio
async def test_children_inherit_the_access_of_the_parent(empty_server) -> None:
    parameters = (await empty_server.add_machine(PartMachine())).parameter_set
    current, fixed = parameters.Current, parameters.Fixed

    leaf = current["Size"]["Edge"]["Radius"]
    assert leaf.bypass_lock is True
    assert leaf.minimum_access_level == current.minimum_access_level
    assert fixed["Size"].minimum_access_level == 3
    assert fixed["Size"]["Length"].minimum_access_level == 3
    assert leaf._writable == current._writable is True
    assert fixed["Size"]["Length"]._writable == fixed._writable
