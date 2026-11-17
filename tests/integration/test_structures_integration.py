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
from asyncua.ua.status_codes import StatusCodes
from asyncua.ua.ua_binary import extensionobject_from_binary, extensionobject_to_binary

from opensmi.server import BaseMachine, ParameterSet, UaVariable
from opensmi.server.errors import TypeConflictError
from opensmi.server.mixins import ParameterSetMixin
from opensmi.server.structures import _create, _fields


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
    node_id = await empty_server.ensure_structure(Point, namespace_index=namespace)

    assert node_id == ua.NodeId("Point", namespace)
    node = empty_server.ua_server.get_node(node_id)
    assert (await node.read_node_class()) == ua.NodeClass.DataType
    assert (await node.read_browse_name()) == ua.QualifiedName("Point", namespace)
    parents = await node.get_references(refs=ua.ObjectIds.HasSubtype, direction=ua.BrowseDirection.Inverse)
    assert [each.NodeId for each in parents] == [ua.NodeId(ObjectIds.Structure)]


@pytest.mark.asyncio
async def test_the_namespace_defaults_to_one(empty_server) -> None:
    assert await empty_server.ensure_structure(Point) == ua.NodeId("Point", 1)
    assert await exists(empty_server.ua_server, ua.NodeId("Point.DefaultBinary", 1))


@pytest.mark.asyncio
async def test_the_definition_names_the_encoding_and_maps_the_type_hints(empty_server, namespace) -> None:
    await empty_server.ensure_structure(Shape, namespace_index=namespace)

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
    await empty_server.ensure_structure(Shape, namespace_index=namespace)

    assert await exists(empty_server.ua_server, ua.NodeId("Point", namespace))


@pytest.mark.asyncio
async def test_the_encoding_node_is_a_default_binary_object_typed_data_type_encoding(empty_server, namespace) -> None:
    await empty_server.ensure_structure(Point, namespace_index=namespace)

    encoding = empty_server.ua_server.get_node(ua.NodeId("Point.DefaultBinary", namespace))

    assert (await encoding.read_node_class()) == ua.NodeClass.Object
    assert (await encoding.read_browse_name()) == ua.QualifiedName("Default Binary", 0)
    typed = await encoding.get_references(refs=ua.ObjectIds.HasTypeDefinition, direction=ua.BrowseDirection.Forward)
    assert [each.NodeId for each in typed] == [ua.NodeId(ObjectIds.DataTypeEncodingType)]
    owners = await encoding.get_references(refs=ua.ObjectIds.HasEncoding, direction=ua.BrowseDirection.Inverse)
    assert [each.NodeId for each in owners] == [ua.NodeId("Point", namespace)]


@pytest.mark.asyncio
async def test_register_is_idempotent(empty_server, namespace) -> None:
    first = await empty_server.ensure_structure(Shape, namespace_index=namespace)
    second = await empty_server.ensure_structure(Shape, namespace_index=namespace)
    point = await empty_server.ensure_structure(Point, namespace_index=namespace)

    assert first == second == ua.NodeId("Shape", namespace)
    assert point == ua.NodeId("Point", namespace)
    children = await empty_server.ua_server.nodes.base_structure_type.get_children()
    assert sorted(each.nodeid.Identifier for each in children if each.nodeid.NamespaceIndex == namespace) == [
        "Point",
        "Shape",
    ]


@pytest.mark.asyncio
async def test_a_different_class_with_the_same_name_and_fields_is_refused(empty_server, namespace) -> None:
    # asyncua decodes an encoding into one class: a twin would take the values of the first class
    await empty_server.ensure_structure(Point, namespace_index=namespace)
    twin = make_dataclass("Point", [("x", ua.Double, ua.Double(0.0)), ("y", ua.Double, ua.Double(0.0))])

    with pytest.raises(TypeConflictError, match="same fields"):
        await empty_server.ensure_structure(twin, namespace_index=namespace)

    assert ua.extension_objects_by_typeid[ua.NodeId("Point.DefaultBinary", namespace)] is Point


@pytest.mark.asyncio
async def test_a_twin_nested_in_a_new_structure_is_refused(empty_server, namespace) -> None:
    await empty_server.ensure_structure(Point, namespace_index=namespace)
    twin = make_dataclass("Point", [("x", ua.Double, ua.Double(0.0)), ("y", ua.Double, ua.Double(0.0))])
    wrapper = make_dataclass("TwinWrapper", [("inner", twin)])

    with pytest.raises(TypeConflictError, match="Point"):
        await empty_server.ensure_structure(wrapper, namespace_index=namespace)

    assert not await exists(empty_server.ua_server, ua.NodeId("TwinWrapper", namespace))


@pytest.mark.asyncio
async def test_a_different_class_under_a_taken_name_is_refused(empty_server, namespace) -> None:
    await empty_server.ensure_structure(Point, namespace_index=namespace)
    other = make_dataclass("Point", [("x", ua.Float, ua.Float(0.0))])

    with pytest.raises(TypeConflictError, match="Point"):
        await empty_server.ensure_structure(other, namespace_index=namespace)


@pytest.mark.asyncio
async def test_a_nested_conflict_is_refused_before_anything_is_created(empty_server, namespace) -> None:
    await empty_server.ensure_structure(Point, namespace_index=namespace)
    other = make_dataclass("Point", [("x", ua.Float, ua.Float(0.0))])
    wrapper = make_dataclass("Wrapper", [("inner", other)])

    with pytest.raises(TypeConflictError, match="Point"):
        await empty_server.ensure_structure(wrapper, namespace_index=namespace)

    assert not await exists(empty_server.ua_server, ua.NodeId("Wrapper", namespace))


@pytest.mark.asyncio
async def test_two_classes_of_one_name_within_one_structure_are_refused(empty_server, namespace) -> None:
    other = make_dataclass("Point", [("z", ua.Double, ua.Double(0.0))])
    clash = make_dataclass("Clash", [("a", Point), ("b", other)])

    with pytest.raises(TypeConflictError, match="Point"):
        await empty_server.ensure_structure(clash, namespace_index=namespace)

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
        await empty_server.ensure_structure(odd, namespace_index=namespace)

    assert not await exists(empty_server.ua_server, ua.NodeId("Odd", namespace))


@pytest.mark.asyncio
async def test_something_that_is_not_a_dataclass_is_refused(empty_server, namespace) -> None:
    with pytest.raises(TypeError, match="dataclass"):
        await empty_server.ensure_structure(dict, namespace_index=namespace)


@pytest.mark.asyncio
async def test_a_dataclass_made_at_runtime_works_like_a_written_one(empty_server, namespace) -> None:
    pose = make_dataclass(
        "Pose",
        [("frame", ua.String, ua.String("map")), ("position", Point, field(default_factory=Point))],
    )

    node_id = await empty_server.ensure_structure(pose, namespace_index=namespace)
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
    node_id = await empty_server.ensure_structure(Shape, namespace_index=namespace)
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
    await empty_server.ensure_structure(Shape, namespace_index=namespace)

    wire = extensionobject_to_binary(SHAPE_VALUE)
    received = extensionobject_from_binary(Buffer(wire))

    assert received == SHAPE_VALUE
    assert type(received) is Shape
    assert extensionobject_from_binary(Buffer(wire)).corners[2] == Point(0.0, 1.0)


@pytest.mark.asyncio
async def test_a_client_can_read_the_definition_without_generating_classes(empty_server, namespace) -> None:
    ua_server = empty_server.ua_server
    await empty_server.ensure_structure(Point, namespace_index=namespace)

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
    await empty_server.ensure_structure(Plain, namespace_index=namespace)

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


@dataclass
class Gauge:
    reading: ua.Double = 0.0


class GaugeMachine(BaseMachine):
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("1")
        await self.identification.ProductInstanceUri.write("urn:example:1")
        await self.identification.Manufacturer.write(ua.LocalizedText("Example", "en"))


class GaugeParameters(ParameterSet):
    Plain = UaVariable(initial_value=Gauge(2.0), writable=True, bypass_lock=True)


class PlainGaugeMachine(ParameterSetMixin[GaugeParameters], GaugeMachine):
    pass


@pytest.mark.asyncio
async def test_an_explicit_ua_data_type_is_used_and_no_second_type_is_minted(empty_server, namespace) -> None:
    node_id = await empty_server.ensure_structure(Gauge, namespace_index=namespace)

    class Explicit(ParameterSet):
        Chosen = UaVariable(initial_value=Gauge(1.0), ua_data_type=node_id, writable=True, bypass_lock=True)

    class ExplicitMachine(ParameterSetMixin[Explicit], GaugeMachine):
        pass

    machine = await empty_server.add_machine(ExplicitMachine())

    assert (await machine.parameter_set.Chosen.ua_node.read_data_type()) == node_id
    assert not await exists(empty_server.ua_server, ua.NodeId("Gauge", 1))
    assert (await machine.parameter_set.Chosen.read()) == Gauge(1.0)


@pytest.mark.asyncio
async def test_a_class_that_has_a_data_type_keeps_it(empty_server, namespace) -> None:
    node_id = await empty_server.ensure_structure(Gauge, namespace_index=namespace)

    machine = await empty_server.add_machine(PlainGaugeMachine())

    assert (await machine.parameter_set.Plain.ua_node.read_data_type()) == node_id
    assert not await exists(empty_server.ua_server, ua.NodeId("Gauge", 1))


@pytest.mark.asyncio
async def test_a_class_without_a_data_type_gets_one_in_namespace_one(empty_server) -> None:
    machine = await empty_server.add_machine(PlainGaugeMachine())

    assert (await machine.parameter_set.Plain.ua_node.read_data_type()) == ua.NodeId("Gauge", 1)
    assert await empty_server.ensure_structure(Gauge) == ua.NodeId("Gauge", 1)


@pytest.mark.asyncio
async def test_a_scalar_structure_variable_refuses_other_values(empty_server) -> None:
    machine = await empty_server.add_machine(PlainGaugeMachine())
    plain = machine.parameter_set.Plain

    await plain.write_check(Gauge(3.0))
    for wrong in (Dimensions(), 1.0, [Gauge()]):
        with pytest.raises(ua.UaStatusCodeError) as refused:
            await plain.write_check(wrong)  # pyright: ignore[reportArgumentType]
        assert refused.value.code == StatusCodes.BadTypeMismatch


@pytest.mark.asyncio
async def test_a_write_check_callback_runs_after_the_structure_check(empty_server) -> None:
    seen: list[object] = []

    class Custom(ParameterSet):
        Checked = UaVariable(initial_value=Gauge(), write_check_callback=seen.append, bypass_lock=True)

    class CustomMachine(ParameterSetMixin[Custom], GaugeMachine):
        pass

    machine = await empty_server.add_machine(CustomMachine())
    seen.clear()  # the initial write

    with pytest.raises(ua.UaStatusCodeError) as refused:
        await machine.parameter_set.Checked.write_check(Dimensions())  # pyright: ignore[reportArgumentType]
    assert refused.value.code == StatusCodes.BadTypeMismatch
    await machine.parameter_set.Checked.write_check(Gauge(4.0))
    assert seen == [Gauge(4.0)]


class EmptyParameters(ParameterSet):
    Readings = UaVariable(initial_value=[], variable_type=Measurement, writable=True, bypass_lock=True)


class EmptyMachine(ParameterSetMixin[EmptyParameters], GaugeMachine):
    pass


@pytest.mark.asyncio
async def test_an_empty_list_with_variable_type_declares_an_array_of_the_structure(empty_server) -> None:
    machine = await empty_server.add_machine(EmptyMachine())
    readings = machine.parameter_set.Readings

    assert (await readings.ua_node.read_data_type()) == ua.NodeId("Measurement", 1)
    assert (await readings.ua_node.read_value_rank()) == ua.ValueRank.OneDimension
    assert (await readings.read()) == []
    await readings.write([Measurement("length")])
    assert (await readings.read()) == [Measurement("length")]
    with pytest.raises(ua.UaStatusCodeError):
        await readings.write_check([Dimensions()])  # pyright: ignore[reportArgumentType]


@dataclass
class Fresh:
    value: float = 0.0


@pytest.mark.asyncio
async def test_a_refused_registration_leaves_the_annotations_of_the_class_alone(empty_server, namespace) -> None:
    await empty_server.ensure_structure(Point, namespace_index=namespace)
    other = make_dataclass("Point", [("x", ua.Float, ua.Float(0.0))])
    wrapper = make_dataclass("Wrapper", [("plain", float), ("inner", other)])

    with pytest.raises(TypeConflictError):
        await empty_server.ensure_structure(wrapper, namespace_index=namespace)

    assert wrapper.__annotations__["plain"] is float


@pytest.mark.asyncio
async def test_registering_changes_the_annotations_to_ua_classes(empty_server) -> None:
    await empty_server.ensure_structure(Fresh)

    assert Fresh.__annotations__["value"] is ua.Double


def two_doubles(name: str) -> type:
    return make_dataclass(name, [("x", ua.Double, ua.Double(0.0)), ("y", ua.Double, ua.Double(0.0))])


async def nodeset_type(server, name: str, namespace: int) -> ua.NodeId:
    """Create a structure DataType of two Doubles as an imported nodeset would: the nodes, and no class for it."""
    await _create(server.ua_server, namespace, name, _fields(two_doubles(name)), {})
    return ua.NodeId(name, namespace)


@pytest.mark.asyncio
async def test_register_structure_binds_a_class_to_an_existing_data_type(empty_server, namespace) -> None:
    node_id = await nodeset_type(empty_server, "Remote", namespace)
    elsewhere = two_doubles("Elsewhere")

    await empty_server.register_structure(elsewhere, ua_data_type=node_id)

    assert not await exists(empty_server.ua_server, ua.NodeId("Elsewhere", namespace))
    assert not await exists(empty_server.ua_server, ua.NodeId("Elsewhere", 1))
    received = extensionobject_from_binary(Buffer(extensionobject_to_binary(elsewhere(1.0, 2.0))))
    assert type(received) is elsewhere
    assert received == elsewhere(1.0, 2.0)


@pytest.mark.asyncio
async def test_a_bound_class_is_used_by_variables(empty_server, namespace) -> None:
    node_id = await nodeset_type(empty_server, "Remote", namespace)
    bound = two_doubles("Bound")
    await empty_server.register_structure(bound, ua_data_type=node_id)

    class BoundParameters(ParameterSet):
        Value = UaVariable(initial_value=bound(1.0, 2.0), writable=True, bypass_lock=True)

    class BoundMachine(ParameterSetMixin[BoundParameters], GaugeMachine):
        pass

    machine = await empty_server.add_machine(BoundMachine())

    assert (await machine.parameter_set.Value.ua_node.read_data_type()) == node_id
    assert (await machine.parameter_set.Value.read()) == bound(1.0, 2.0)
    assert not await exists(empty_server.ua_server, ua.NodeId("Bound", 1))


@pytest.mark.asyncio
async def test_register_structure_refuses_a_data_type_without_default_binary_encoding(empty_server, namespace) -> None:
    bare = await empty_server.ua_server.nodes.base_structure_type.add_data_type(
        ua.NodeId("Bare", namespace), ua.QualifiedName("Bare", namespace)
    )

    with pytest.raises(ValueError, match="Default Binary"):
        await empty_server.register_structure(Point, ua_data_type=bare.nodeid)


@pytest.mark.asyncio
async def test_a_bound_class_nested_in_a_created_one_keeps_its_data_type(empty_server, namespace) -> None:
    bound_type = await nodeset_type(empty_server, "Size", namespace)
    size = two_doubles("Size")
    await empty_server.register_structure(size, ua_data_type=bound_type)
    part = make_dataclass("Part", [("size", size)])

    part_type = await empty_server.ensure_structure(part)

    assert not await exists(empty_server.ua_server, ua.NodeId("Size", 1))
    assert empty_server._types.by_class[size] == bound_type
    assert ua.typeid_by_extension_objects[size] == ua.NodeId("Size.DefaultBinary", namespace)
    definition = await empty_server.ua_server.get_node(part_type).read_data_type_definition()
    assert [each.DataType for each in definition.Fields] == [bound_type]


@pytest.mark.asyncio
async def test_a_class_that_has_a_data_type_is_given_it_back(empty_server, namespace) -> None:
    spot = two_doubles("Spot")
    known = await empty_server.ensure_structure(spot, namespace_index=namespace)

    assert await empty_server.ensure_structure(spot) == known
    assert not await exists(empty_server.ua_server, ua.NodeId("Spot", 1))


@pytest.mark.asyncio
async def test_a_class_is_refused_a_second_data_type_in_another_namespace(empty_server, namespace) -> None:
    spot = two_doubles("Spot")
    first = await empty_server.ensure_structure(spot)

    with pytest.raises(TypeConflictError, match="namespace"):
        await empty_server.ensure_structure(spot, namespace_index=namespace)

    assert not await exists(empty_server.ua_server, ua.NodeId("Spot", namespace))
    assert empty_server._types.by_class[spot] == first
    assert ua.typeid_by_extension_objects[spot] == ua.NodeId("Spot.DefaultBinary", 1)


@pytest.mark.asyncio
async def test_register_structure_refuses_a_class_that_has_another_data_type(empty_server, namespace) -> None:
    mark = two_doubles("Mark")
    first = await empty_server.ensure_structure(mark)
    other = await empty_server.ensure_structure(two_doubles("Other"), namespace_index=namespace)

    with pytest.raises(TypeConflictError, match="Mark"):
        await empty_server.register_structure(mark, ua_data_type=other)
    await empty_server.register_structure(mark, ua_data_type=first)  # the same one again is fine

    assert empty_server._types.by_class[mark] == first
    assert ua.typeid_by_extension_objects[mark] == ua.NodeId("Mark.DefaultBinary", 1)


@pytest.mark.asyncio
async def test_register_structure_refuses_a_data_type_that_another_class_has(empty_server, namespace) -> None:
    owner = two_doubles("Owner")
    owned = await empty_server.ensure_structure(owner, namespace_index=namespace)
    twin = two_doubles("Owner")

    with pytest.raises(TypeConflictError, match="already belongs"):
        await empty_server.register_structure(twin, ua_data_type=owned)

    assert ua.extension_objects_by_typeid[ua.NodeId("Owner.DefaultBinary", namespace)] is owner


class OptionalParameters(ParameterSet):
    Maybe = UaVariable(initial_value=Gauge(), optional_ok=True, writable=True, bypass_lock=True)


class OptionalMachine(ParameterSetMixin[OptionalParameters], GaugeMachine):
    pass


@pytest.mark.asyncio
async def test_an_optional_structure_variable_accepts_none(empty_server) -> None:
    machine = await empty_server.add_machine(OptionalMachine())

    await machine.parameter_set.Maybe.write_check(None)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ([("x", ua.Double, ua.Double(0.0)), ("z", ua.Double, ua.Double(0.0))], "fields"),
        ([("y", ua.Double, ua.Double(0.0)), ("x", ua.Double, ua.Double(0.0))], "fields"),
        ([("x", ua.Double, ua.Double(0.0))], "fields"),
        ([("x", ua.Float, ua.Float(0.0)), ("y", ua.Double, ua.Double(0.0))], "'x' is Float, the DataType has Double"),
        ([("x", list[ua.Double]), ("y", ua.Double, ua.Double(0.0))], "'x' has the ValueRank"),
    ],
    ids=["other name", "other order", "missing field", "other type", "array"],
)
async def test_register_structure_refuses_a_class_that_differs_from_the_definition(
    empty_server, namespace, fields, message
) -> None:
    remote = await nodeset_type(empty_server, "Remote", namespace)
    differs = make_dataclass("Differs", fields)

    with pytest.raises(TypeConflictError, match=message):
        await empty_server.register_structure(differs, ua_data_type=remote)

    assert differs not in empty_server._types.by_class


@pytest.mark.asyncio
async def test_register_structure_accepts_a_field_type_that_encodes_the_same(empty_server, namespace) -> None:
    remote = await nodeset_type(empty_server, "Remote", namespace)
    node = empty_server.ua_server.get_node(remote)
    definition = await node.read_data_type_definition()
    definition.Fields[0].DataType = ua.NodeId(ua.ObjectIds.Duration)  # a subtype of Double
    await node.write_data_type_definition(definition)

    await empty_server.register_structure(two_doubles("Timed"), ua_data_type=remote)


async def nodeset_wrapper(server, namespace: int, inner: ua.NodeId) -> ua.NodeId:
    """A nodeset structure Wrapper with one field of the structure ``inner``."""
    await _create(server.ua_server, namespace, "Wrapper", _fields(make_dataclass("Wrapper", [("inner", Point)])), {})
    node = server.ua_server.get_node(ua.NodeId("Wrapper", namespace))
    definition = await node.read_data_type_definition()
    definition.Fields[0].DataType = inner
    await node.write_data_type_definition(definition)
    return ua.NodeId("Wrapper", namespace)


@pytest.mark.asyncio
async def test_register_structure_refuses_a_nested_class_without_a_data_type(empty_server, namespace) -> None:
    inner = await nodeset_type(empty_server, "Inner", namespace)
    wrapper = await nodeset_wrapper(empty_server, namespace, inner)
    unbound = two_doubles("Inner")

    with pytest.raises(TypeConflictError, match="bind it first"):
        await empty_server.register_structure(make_dataclass("Wrapper", [("inner", unbound)]), ua_data_type=wrapper)


@pytest.mark.asyncio
async def test_register_structure_binds_nested_classes_bound_first(empty_server, namespace) -> None:
    inner = await nodeset_type(empty_server, "Inner", namespace)
    wrapper = await nodeset_wrapper(empty_server, namespace, inner)
    bound_inner = two_doubles("Inner")
    await empty_server.register_structure(bound_inner, ua_data_type=inner)
    bound_wrapper = make_dataclass("Wrapper", [("inner", bound_inner)])

    await empty_server.register_structure(bound_wrapper, ua_data_type=wrapper)

    value = bound_wrapper(bound_inner(1.0, 2.0))
    assert extensionobject_from_binary(Buffer(extensionobject_to_binary(value))) == value


@pytest.mark.asyncio
async def test_register_structure_refuses_a_nested_class_with_another_data_type(empty_server, namespace) -> None:
    inner = await nodeset_type(empty_server, "Inner", namespace)
    wrapper = await nodeset_wrapper(empty_server, namespace, inner)
    elsewhere = two_doubles("Elsewhere")
    await empty_server.ensure_structure(elsewhere)

    with pytest.raises(TypeConflictError, match="Elsewhere"):
        await empty_server.register_structure(make_dataclass("Wrapper", [("inner", elsewhere)]), ua_data_type=wrapper)


@pytest.mark.asyncio
async def test_register_structure_refuses_a_data_type_without_definition(empty_server, namespace) -> None:
    remote = await nodeset_type(empty_server, "Remote", namespace)
    del empty_server.ua_server.iserver.aspace[remote].attributes[ua.AttributeIds.DataTypeDefinition]

    with pytest.raises(ValueError, match="DataTypeDefinition"):
        await empty_server.register_structure(two_doubles("Remote"), ua_data_type=remote)


@pytest.mark.asyncio
async def test_register_structure_overwrite_rebinds_the_class(empty_server, namespace) -> None:
    first = await nodeset_type(empty_server, "First", namespace)
    second = await nodeset_type(empty_server, "Second", namespace)
    moving = two_doubles("Moving")
    await empty_server.register_structure(moving, ua_data_type=first)

    await empty_server.register_structure(moving, ua_data_type=second, overwrite=True)

    assert empty_server._types.by_class[moving] == second
    assert (namespace, "First") not in empty_server._types.by_name
    assert empty_server._types.by_name[namespace, "Second"] is moving
    assert ua.typeid_by_extension_objects[moving] == ua.NodeId("Second.DefaultBinary", namespace)
