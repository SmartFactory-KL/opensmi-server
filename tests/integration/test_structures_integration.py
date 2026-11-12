# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import sys
import textwrap
from dataclasses import dataclass, field, make_dataclass

import pytest
import pytest_asyncio
from asyncua import ua
from asyncua.client.client import Client
from asyncua.common.utils import Buffer
from asyncua.ua.object_ids import ObjectIds
from asyncua.ua.ua_binary import extensionobject_from_binary, extensionobject_to_binary

from opensmi.server import BaseMachine, ParameterSet, UaVariable
from opensmi.server.mixins import ParameterSetMixin
from opensmi.server.structures import StructureConflictError, register_structure


@dataclass
class Point:
    x: ua.Double = 0.0
    y: ua.Double = 0.0


@dataclass
class Shape:
    label: ua.String = ""
    corners: list[Point] = field(default_factory=list)
    weights: list[ua.Double] = field(default_factory=list)
    tag: ua.ByteString = b""
    sides: ua.Int32 = 0
    closed: ua.Boolean = False


SHAPE_VALUE = Shape("triangle", [Point(0.0, 0.0), Point(1.0, 0.0), Point(0.0, 1.0)], [0.25, 0.75], b"\x01\x02", 3, True)

# A client that has never heard of the structure: it learns it from the server's DataTypeDefinition. It runs in
# its own process because asyncua keeps a process-global table of the classes it knows, so a client in the
# server's process would use the server's own classes and prove nothing.
CLIENT = textwrap.dedent("""
    import asyncio, json, logging, sys
    from asyncua.client.client import Client

    logging.disable(logging.CRITICAL)

    async def main(url, nodeid):
        async with Client(url) as client:
            await client.load_data_type_definitions()
            node = client.get_node(nodeid)
            first = (await node.read_value())[0]
            print(json.dumps({
                "class": type(first).__name__,
                "label": first.label,
                "corners": [[each.x, each.y] for each in first.corners],
                "weights": list(first.weights),
                "tag": bytes(first.tag).hex(),
                "sides": first.sides,
                "closed": first.closed,
            }))

    asyncio.run(main(sys.argv[1], sys.argv[2]))
""")


@pytest_asyncio.fixture
async def namespace(empty_server) -> int:
    return await empty_server.ua_server.register_namespace("urn:example:structures")


async def exists(server, node_id: ua.NodeId) -> bool:
    try:
        await server.get_node(node_id).read_browse_name()
    except ua.UaStatusCodeError:
        return False
    return True


@pytest.mark.asyncio
async def test_register_creates_the_data_type_under_base_structure(empty_server, namespace) -> None:
    node_id = await register_structure(empty_server.ua_server, Point, namespace)

    assert node_id == ua.NodeId("Point", namespace)
    node = empty_server.ua_server.get_node(node_id)
    assert (await node.read_node_class()) == ua.NodeClass.DataType
    assert (await node.read_browse_name()) == ua.QualifiedName("Point", namespace)
    parents = await node.get_references(refs=ua.ObjectIds.HasSubtype, direction=ua.BrowseDirection.Inverse)
    assert [each.NodeId for each in parents] == [ua.NodeId(ObjectIds.Structure)]


@pytest.mark.asyncio
async def test_register_accepts_the_opensmi_server_as_well(empty_server, namespace) -> None:
    assert await register_structure(empty_server, Point, namespace) == ua.NodeId("Point", namespace)


@pytest.mark.asyncio
async def test_the_namespace_defaults_to_the_one_registered_last(empty_server, namespace) -> None:
    assert await register_structure(empty_server.ua_server, Point) == ua.NodeId("Point", namespace)


@pytest.mark.asyncio
async def test_the_definition_names_the_encoding_and_maps_the_type_hints(empty_server, namespace) -> None:
    await register_structure(empty_server.ua_server, Shape, namespace)

    definition = await empty_server.ua_server.get_node(ua.NodeId("Shape", namespace)).read_data_type_definition()

    assert definition.DefaultEncodingId == ua.NodeId("Shape.DefaultBinary", namespace)
    assert definition.BaseDataType == ua.NodeId(ObjectIds.Structure)
    assert definition.StructureType == ua.StructureType.Structure
    assert [
        (each.Name, each.DataType, each.ValueRank, list(each.ArrayDimensions), each.IsOptional)
        for each in definition.Fields
    ] == [
        ("label", ua.NodeId(ObjectIds.String), -1, [], False),
        ("corners", ua.NodeId("Point", namespace), 1, [0], False),
        ("weights", ua.NodeId(ObjectIds.Double), 1, [0], False),
        ("tag", ua.NodeId(ObjectIds.ByteString), -1, [], False),
        ("sides", ua.NodeId(ObjectIds.Int32), -1, [], False),
        ("closed", ua.NodeId(ObjectIds.Boolean), -1, [], False),
    ]


@pytest.mark.asyncio
async def test_nested_structures_are_registered_too(empty_server, namespace) -> None:
    await register_structure(empty_server.ua_server, Shape, namespace)

    assert await exists(empty_server.ua_server, ua.NodeId("Point", namespace))


@pytest.mark.asyncio
async def test_the_encoding_node_is_a_default_binary_object_typed_data_type_encoding(empty_server, namespace) -> None:
    await register_structure(empty_server.ua_server, Point, namespace)

    encoding = empty_server.ua_server.get_node(ua.NodeId("Point.DefaultBinary", namespace))

    assert (await encoding.read_node_class()) == ua.NodeClass.Object
    assert (await encoding.read_browse_name()) == ua.QualifiedName("Default Binary", 0)
    typed = await encoding.get_references(refs=ua.ObjectIds.HasTypeDefinition, direction=ua.BrowseDirection.Forward)
    assert [each.NodeId for each in typed] == [ua.NodeId(ObjectIds.DataTypeEncodingType)]
    owners = await encoding.get_references(refs=ua.ObjectIds.HasEncoding, direction=ua.BrowseDirection.Inverse)
    assert [each.NodeId for each in owners] == [ua.NodeId("Point", namespace)]


@pytest.mark.asyncio
async def test_register_is_idempotent(empty_server, namespace) -> None:
    first = await register_structure(empty_server.ua_server, Shape, namespace)
    second = await register_structure(empty_server.ua_server, Shape, namespace)
    point = await register_structure(empty_server.ua_server, Point, namespace)

    assert first == second == ua.NodeId("Shape", namespace)
    assert point == ua.NodeId("Point", namespace)
    children = await empty_server.ua_server.nodes.base_structure_type.get_children()
    assert sorted(each.nodeid.Identifier for each in children if each.nodeid.NamespaceIndex == namespace) == [
        "Point",
        "Shape",
    ]


@pytest.mark.asyncio
async def test_an_equal_class_registers_again_and_encodes(empty_server, namespace) -> None:
    await register_structure(empty_server.ua_server, Point, namespace)
    again = make_dataclass("Point", [("x", ua.Double, ua.Double(0.0)), ("y", ua.Double, ua.Double(0.0))])

    await register_structure(empty_server.ua_server, again, namespace)

    variable = await empty_server.ua_server.nodes.objects.add_variable(
        ua.NodeId("again", namespace), ua.QualifiedName("again", namespace), again(1.0, 2.0)
    )
    assert (await variable.read_value()) == again(1.0, 2.0)


@pytest.mark.asyncio
async def test_a_different_class_under_a_taken_name_is_refused(empty_server, namespace) -> None:
    await register_structure(empty_server.ua_server, Point, namespace)
    other = make_dataclass("Point", [("x", ua.Float, ua.Float(0.0))])

    with pytest.raises(StructureConflictError, match="Point"):
        await register_structure(empty_server.ua_server, other, namespace)


@pytest.mark.asyncio
async def test_a_nested_conflict_is_refused_before_anything_is_created(empty_server, namespace) -> None:
    await register_structure(empty_server.ua_server, Point, namespace)
    other = make_dataclass("Point", [("x", ua.Float, ua.Float(0.0))])
    wrapper = make_dataclass("Wrapper", [("inner", other)])

    with pytest.raises(StructureConflictError, match="Point"):
        await register_structure(empty_server.ua_server, wrapper, namespace)

    assert not await exists(empty_server.ua_server, ua.NodeId("Wrapper", namespace))


@pytest.mark.asyncio
async def test_two_classes_of_one_name_within_one_structure_are_refused(empty_server, namespace) -> None:
    other = make_dataclass("Point", [("z", ua.Double, ua.Double(0.0))])
    clash = make_dataclass("Clash", [("a", Point), ("b", other)])

    with pytest.raises(StructureConflictError, match="Point"):
        await register_structure(empty_server.ua_server, clash, namespace)

    assert not await exists(empty_server.ua_server, ua.NodeId("Point", namespace))
    assert not await exists(empty_server.ua_server, ua.NodeId("Clash", namespace))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hint", "message"),
    [
        (object, "object"),
        (set[ua.Double], "set"),
        (ua.NodeId, "NodeId"),
        (ua.Double | None, "Optional"),
        (list[list[ua.Double]], "list"),
        (dict[str, ua.Double], "dict"),
        (list[object], "object"),
    ],
)
async def test_unsupported_field_types_are_refused_with_the_field_named(empty_server, namespace, hint, message) -> None:
    odd = make_dataclass("Odd", [("wrong", hint)])

    with pytest.raises(TypeError, match=rf"wrong.*{message}|{message}.*wrong"):
        await register_structure(empty_server.ua_server, odd, namespace)

    assert not await exists(empty_server.ua_server, ua.NodeId("Odd", namespace))


@pytest.mark.asyncio
async def test_something_that_is_not_a_dataclass_is_refused(empty_server, namespace) -> None:
    with pytest.raises(TypeError, match="dataclass"):
        await register_structure(empty_server.ua_server, dict, namespace)


@pytest.mark.asyncio
async def test_a_dataclass_made_at_runtime_works_like_a_written_one(empty_server, namespace) -> None:
    pose = make_dataclass(
        "Pose",
        [("frame", ua.String, ua.String("map")), ("position", Point, field(default_factory=Point))],
    )

    node_id = await register_structure(empty_server.ua_server, pose, namespace)
    variable = await empty_server.ua_server.nodes.objects.add_variable(
        ua.NodeId("pose", namespace),
        ua.QualifiedName("pose", namespace),
        pose("odom", Point(1.0, 2.0)),
        datatype=node_id,
    )

    assert (await variable.read_value()) == pose("odom", Point(1.0, 2.0))
    assert await exists(empty_server.ua_server, ua.NodeId("Point", namespace))


@pytest.mark.asyncio
async def test_a_client_round_trips_a_value_through_load_data_type_definitions(empty_server, namespace) -> None:
    ua_server = empty_server.ua_server
    node_id = await register_structure(ua_server, Shape, namespace)
    variable = await ua_server.nodes.objects.add_variable(
        ua.NodeId("shapes", namespace),
        ua.QualifiedName("shapes", namespace),
        ua.Variant([SHAPE_VALUE], ua.VariantType.ExtensionObject),
        datatype=node_id,
    )

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        CLIENT,
        f"opc.tcp://operator:operator@localhost:{ua_server.bserver.port}",
        variable.nodeid.to_string(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(process.communicate(), timeout=60)

    assert process.returncode == 0, err.decode()
    assert json.loads(out) == {
        "class": "Shape",
        "label": "triangle",
        "corners": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
        "weights": [0.25, 0.75],
        "tag": "0102",
        "sides": 3,
        "closed": True,
    }


@pytest.mark.asyncio
async def test_registered_classes_travel_as_extension_objects(empty_server, namespace) -> None:
    await register_structure(empty_server.ua_server, Shape, namespace)

    wire = extensionobject_to_binary(SHAPE_VALUE)
    received = extensionobject_from_binary(Buffer(wire))

    assert received == SHAPE_VALUE
    assert type(received) is Shape
    assert extensionobject_from_binary(Buffer(wire)).corners[2] == Point(0.0, 1.0)


@pytest.mark.asyncio
async def test_a_client_can_read_the_definition_without_generating_classes(empty_server, namespace) -> None:
    ua_server = empty_server.ua_server
    await register_structure(ua_server, Point, namespace)

    async with Client(f"opc.tcp://operator:operator@localhost:{ua_server.bserver.port}") as client:
        definition = await client.get_node(ua.NodeId("Point", namespace)).read_data_type_definition()
        encoding = client.get_node(ua.NodeId("Point.DefaultBinary", namespace))
        typed = await encoding.get_references(refs=ua.ObjectIds.HasTypeDefinition)

    assert [each.Name for each in definition.Fields] == ["x", "y"]
    assert [each.NodeId for each in typed] == [ua.NodeId(ObjectIds.DataTypeEncodingType)]


@dataclass
class Plain:
    a: float = 0.0
    b: int = 0
    c: str = ""
    d: bool = False
    e: bytes = b""
    f: list[float] = field(default_factory=list)
    g: list[Point] = field(default_factory=list)


@pytest.mark.asyncio
async def test_plain_annotations_stand_for_the_ua_types(empty_server, namespace) -> None:
    await register_structure(empty_server.ua_server, Plain, namespace)

    definition = await empty_server.ua_server.get_node(ua.NodeId("Plain", namespace)).read_data_type_definition()
    value = Plain(1.5, 2, "three", True, b"", [5.5], [Point(6.0, 7.0)])

    assert [(each.DataType, each.ValueRank) for each in definition.Fields] == [
        (ua.NodeId(ObjectIds.Double), -1),
        (ua.NodeId(ObjectIds.Int64), -1),
        (ua.NodeId(ObjectIds.String), -1),
        (ua.NodeId(ObjectIds.Boolean), -1),
        (ua.NodeId(ObjectIds.ByteString), -1),
        (ua.NodeId(ObjectIds.Double), 1),
        (ua.NodeId("Point", namespace), 1),
    ]
    assert extensionobject_from_binary(Buffer(extensionobject_to_binary(value))) == value


@dataclass
class Dimensions:
    length: float = 0.0
    width: float = 0.0


@dataclass
class Workpiece:
    id: str = ""
    dimensions: Dimensions = field(default_factory=Dimensions)


@dataclass
class Measurement:
    name: str = ""
    in_tolerance: bool = True


class DeclaredParameters(ParameterSet):
    Current = UaVariable(initial_value=Workpiece("A1", Dimensions(100.0, 50.0)), bypass_lock=True, writable=True)
    Results = UaVariable(initial_value=[Measurement("length")], bypass_lock=True, writable=True)


class DeclaredMachine(ParameterSetMixin[DeclaredParameters], BaseMachine):
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("1")
        await self.identification.ProductInstanceUri.write("urn:example:1")
        await self.identification.Manufacturer.write(ua.LocalizedText("Example", "en"))


@pytest.mark.asyncio
async def test_a_variable_with_a_dataclass_value_registers_the_structure_itself(empty_server) -> None:
    machine = await empty_server.add_machine(DeclaredMachine())
    current, results = machine.parameter_set.Current, machine.parameter_set.Results

    assert (await current.ua_node.read_data_type()) == ua.NodeId("Workpiece", 1)
    assert (await results.ua_node.read_data_type()) == ua.NodeId("Measurement", 1)
    assert (await results.ua_node.read_value_rank()) == ua.ValueRank.OneDimension
    assert await exists(empty_server.ua_server, ua.NodeId("Dimensions", 1))
    assert (await current.read()) == Workpiece("A1", Dimensions(100.0, 50.0))
    await current.write(Workpiece("B2", Dimensions(10.0, 5.0)))
    assert (await current.read()) == Workpiece("B2", Dimensions(10.0, 5.0))
    await results.write([Measurement("width", in_tolerance=False), Measurement("height")])
    assert (await results.read())[0] == Measurement("width", in_tolerance=False)
    with pytest.raises(ua.UaStatusCodeError):
        await results.write_check([Dimensions()])  # pyright: ignore[reportArgumentType]
