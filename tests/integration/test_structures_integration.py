# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio
import json
import sys
import textwrap

import pytest
import pytest_asyncio
from asyncua import ua
from asyncua.client.client import Client
from asyncua.common.utils import Buffer
from asyncua.ua.object_ids import ObjectIds
from asyncua.ua.ua_binary import extensionobject_from_binary

from opensmi.server.structures import (
    StructureConflictError,
    StructureDefinition,
    StructureField,
    StructureRegistry,
    decode,
    encode,
    encoding_node_id,
    registry_for,
    structure_node_id,
)

URI = "urn:example:structures"

POINT = StructureDefinition(
    type_name="Point",
    fields=(
        StructureField(name="x", variant_type=ua.VariantType.Double),
        StructureField(name="y", variant_type=ua.VariantType.Double),
    ),
)
SHAPE = StructureDefinition(
    type_name="Shape",
    fields=(
        StructureField(name="label", variant_type=ua.VariantType.String),
        StructureField(name="corners", variant_type=ua.VariantType.ExtensionObject, datatype=POINT, array=True),
        StructureField(name="weights", variant_type=ua.VariantType.Double, array=True, length=2),
        StructureField(name="tag", variant_type=ua.VariantType.ByteString),
    ),
)

SHAPE_VALUE = {
    "label": "triangle",
    "corners": [{"x": 0.0, "y": 0.0}, {"x": 1.0, "y": 0.0}, {"x": 0.0, "y": 1.0}],
    "weights": [0.25, 0.75],
    "tag": b"\x01\x02",
}

# A client that has never heard of the structure: it learns it from the server's DataTypeDefinition. It runs in
# its own process because asyncua keeps a process-global table of the classes it generated, so a client in the
# server's process could not tell a server that describes its types from one that merely shares its classes.
CLIENT = textwrap.dedent("""
    import asyncio, json, logging, sys
    from asyncua.client.client import Client
    from asyncua.ua.ua_binary import extensionobject_to_binary

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
                "wire": extensionobject_to_binary(first).hex(),
            }))

    asyncio.run(main(sys.argv[1], sys.argv[2]))
""")


@pytest_asyncio.fixture
async def namespace(empty_server) -> int:
    return await empty_server.ua_server.register_namespace(URI)


@pytest.mark.asyncio
async def test_mint_creates_the_data_type_under_base_structure(empty_server, namespace) -> None:
    node_id = await StructureRegistry().mint(empty_server.ua_server, namespace, POINT)

    assert node_id == structure_node_id("Point", namespace)
    node = empty_server.ua_server.get_node(node_id)
    assert (await node.read_node_class()) == ua.NodeClass.DataType
    assert (await node.read_browse_name()) == ua.QualifiedName("Point", namespace)
    parents = await node.get_references(refs=ua.ObjectIds.HasSubtype, direction=ua.BrowseDirection.Inverse)
    assert [each.NodeId for each in parents] == [ua.NodeId(ObjectIds.Structure)]


@pytest.mark.asyncio
async def test_mint_writes_the_definition_naming_the_encoding(empty_server, namespace) -> None:
    await StructureRegistry().mint(empty_server.ua_server, namespace, SHAPE)

    node = empty_server.ua_server.get_node(structure_node_id("Shape", namespace))
    definition = await node.read_data_type_definition()

    assert definition.DefaultEncodingId == encoding_node_id("Shape", namespace)
    assert definition.BaseDataType == ua.NodeId(ObjectIds.Structure)
    assert definition.StructureType == ua.StructureType.Structure
    assert [
        (each.Name, each.DataType, each.ValueRank, list(each.ArrayDimensions), each.IsOptional)
        for each in definition.Fields
    ] == [
        ("label", ua.NodeId(ua.ObjectIds.String), -1, [], False),
        ("corners", structure_node_id("Point", namespace), 1, [0], False),
        ("weights", ua.NodeId(ua.ObjectIds.Double), 1, [2], False),
        ("tag", ua.NodeId(ua.ObjectIds.ByteString), -1, [], False),
    ]


@pytest.mark.asyncio
async def test_nested_structures_are_minted_first(empty_server, namespace) -> None:
    registry = StructureRegistry()

    await registry.mint(empty_server.ua_server, namespace, SHAPE)

    assert registry.minted == ("Point", "Shape")
    assert registry.get("Point", namespace) == POINT


@pytest.mark.asyncio
async def test_the_encoding_node_is_a_default_binary_object_typed_data_type_encoding(empty_server, namespace) -> None:
    await StructureRegistry().mint(empty_server.ua_server, namespace, POINT)

    encoding = empty_server.ua_server.get_node(encoding_node_id("Point", namespace))

    assert (await encoding.read_node_class()) == ua.NodeClass.Object
    assert (await encoding.read_browse_name()) == ua.QualifiedName("Default Binary", 0)
    typed = await encoding.get_references(refs=ua.ObjectIds.HasTypeDefinition, direction=ua.BrowseDirection.Forward)
    assert [each.NodeId for each in typed] == [ua.NodeId(ObjectIds.DataTypeEncodingType)]
    owners = await encoding.get_references(refs=ua.ObjectIds.HasEncoding, direction=ua.BrowseDirection.Inverse)
    assert [each.NodeId for each in owners] == [structure_node_id("Point", namespace)]


@pytest.mark.asyncio
async def test_mint_is_idempotent(empty_server, namespace) -> None:
    registry = StructureRegistry()

    first = await registry.mint(empty_server.ua_server, namespace, SHAPE)
    second = await registry.mint(empty_server.ua_server, namespace, SHAPE)
    await registry.mint(empty_server.ua_server, namespace, POINT)

    assert first == second
    assert registry.minted == ("Point", "Shape")


@pytest.mark.asyncio
async def test_registry_for_hands_out_one_registry_per_server(empty_server) -> None:
    assert registry_for(empty_server.ua_server) is registry_for(empty_server.ua_server)


@pytest.mark.asyncio
async def test_a_different_definition_under_one_name_is_refused_before_anything_is_created(
    empty_server, namespace
) -> None:
    registry = StructureRegistry()
    await registry.mint(empty_server.ua_server, namespace, POINT)
    other = StructureDefinition(
        type_name="Point", fields=(StructureField(name="x", variant_type=ua.VariantType.Float),)
    )
    wrapper = StructureDefinition(
        type_name="Wrapper",
        fields=(StructureField(name="p", variant_type=ua.VariantType.ExtensionObject, datatype=other),),
    )

    with pytest.raises(StructureConflictError, match="Point"):
        await registry.mint(empty_server.ua_server, namespace, wrapper)

    assert registry.minted == ("Point",)
    assert registry.get("Wrapper", namespace) is None
    with pytest.raises(ua.UaStatusCodeError):
        await empty_server.ua_server.get_node(structure_node_id("Wrapper", namespace)).read_browse_name()


@pytest.mark.asyncio
async def test_two_definitions_of_one_name_within_one_definition_are_refused(empty_server, namespace) -> None:
    registry = StructureRegistry()
    other = StructureDefinition(
        type_name="Point", fields=(StructureField(name="z", variant_type=ua.VariantType.Double),)
    )
    clash = StructureDefinition(
        type_name="Clash",
        fields=(
            StructureField(name="a", variant_type=ua.VariantType.ExtensionObject, datatype=POINT),
            StructureField(name="b", variant_type=ua.VariantType.ExtensionObject, datatype=other),
        ),
    )

    with pytest.raises(StructureConflictError):
        await registry.mint(empty_server.ua_server, namespace, clash)

    assert registry.minted == ()


@pytest.mark.asyncio
async def test_a_client_round_trips_a_value_through_load_data_type_definitions(empty_server, namespace) -> None:
    ua_server = empty_server.ua_server
    node_id = await StructureRegistry().mint(ua_server, namespace, SHAPE)
    variable = await ua_server.nodes.objects.add_variable(
        ua.NodeId("shapes", namespace),
        ua.QualifiedName("shapes", namespace),
        ua.Variant([encode(SHAPE, SHAPE_VALUE, namespace)], ua.VariantType.ExtensionObject),
        datatype=node_id,
    )
    port = ua_server.bserver.port

    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        "-c",
        CLIENT,
        f"opc.tcp://operator:operator@localhost:{port}",
        variable.nodeid.to_string(),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    out, err = await asyncio.wait_for(process.communicate(), timeout=60)

    assert process.returncode == 0, err.decode()
    seen = json.loads(out)
    wire = seen.pop("wire")
    assert seen == {
        "class": "Shape",
        "label": "triangle",
        "corners": [[0.0, 0.0], [1.0, 0.0], [0.0, 1.0]],
        "weights": [0.25, 0.75],
        "tag": "0102",
    }
    # What the client would send back: its generated class on the wire is read by the codec.
    sent = extensionobject_from_binary(Buffer(bytes.fromhex(wire)))
    assert decode(SHAPE, sent, namespace) == SHAPE_VALUE


@pytest.mark.asyncio
async def test_a_client_can_read_the_definition_without_generating_classes(empty_server, namespace) -> None:
    ua_server = empty_server.ua_server
    await StructureRegistry().mint(ua_server, namespace, POINT)

    async with Client(f"opc.tcp://operator:operator@localhost:{ua_server.bserver.port}") as client:
        definition = await client.get_node(structure_node_id("Point", namespace)).read_data_type_definition()
        encoding = client.get_node(encoding_node_id("Point", namespace))
        typed = await encoding.get_references(refs=ua.ObjectIds.HasTypeDefinition)

    assert [each.Name for each in definition.Fields] == ["x", "y"]
    assert [each.NodeId for each in typed] == [ua.NodeId(ObjectIds.DataTypeEncodingType)]
