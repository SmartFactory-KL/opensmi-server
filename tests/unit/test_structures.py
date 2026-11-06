# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import pytest
from asyncua import ua
from asyncua.common.utils import Buffer
from asyncua.ua.status_codes import StatusCodes
from asyncua.ua.ua_binary import Primitives, extensionobject_from_binary, extensionobject_to_binary

from opensmi.server import UaVariable
from opensmi.server.structures import (
    StructureDefinition,
    StructureField,
    decode,
    encode,
    encoding_node_id,
    structure_array_write_check,
    structure_node_id,
)

NAMESPACE = 3

INNER = StructureDefinition(
    type_name="Inner",
    fields=(
        StructureField(name="x", variant_type=ua.VariantType.Double),
        StructureField(name="y", variant_type=ua.VariantType.Double),
    ),
)
SAMPLE = StructureDefinition(
    type_name="Sample",
    fields=(
        StructureField(name="id", variant_type=ua.VariantType.Int32),
        StructureField(name="label", variant_type=ua.VariantType.String),
        StructureField(name="readings", variant_type=ua.VariantType.Double, array=True),
        StructureField(name="triple", variant_type=ua.VariantType.Double, array=True, length=3),
        StructureField(name="raw", variant_type=ua.VariantType.ByteString),
        StructureField(name="ok", variant_type=ua.VariantType.Boolean),
    ),
)
OUTER = StructureDefinition(
    type_name="Outer",
    fields=(
        StructureField(name="names", variant_type=ua.VariantType.String, array=True),
        StructureField(name="inners", variant_type=ua.VariantType.ExtensionObject, datatype=INNER, array=True),
    ),
)


def sample(identifier: int = 1) -> dict[str, object]:
    return {
        "id": identifier,
        "label": "abc",
        "readings": [0.5, 1.5],
        "triple": [1.0, 2.0, 3.0],
        "raw": b"\x00\x01\xff",
        "ok": True,
    }


def raw(type_id: ua.NodeId, body: bytes) -> ua.ExtensionObject:
    """Return an ExtensionObject as a client without the server's types builds one."""
    carried = ua.ExtensionObject(TypeId=type_id, Body=ua.ByteString(body))
    object.__setattr__(carried, "Encoding", 1)
    return carried


def sample_body(*, readings: bytes | None = None, triple: bytes | None = None) -> bytes:
    return b"".join(
        (
            Primitives.Int32.pack(1),
            Primitives.String.pack("abc"),
            readings if readings is not None else Primitives.Int32.pack(0),
            triple if triple is not None else Primitives.Double.pack_array([1.0, 2.0, 3.0]),
            Primitives.ByteString.pack(b""),
            Primitives.Boolean.pack(False),
        )
    )


# ---------------------------------------------------------------------------
# definitions
# ---------------------------------------------------------------------------


def test_equal_definitions_are_equal() -> None:
    other = StructureDefinition(type_name="Inner", fields=INNER.fields)
    assert other == INNER
    assert hash(other) == hash(INNER)
    assert StructureDefinition(type_name="Inner") != INNER


def test_length_without_array_is_rejected() -> None:
    with pytest.raises(ValueError, match="not an array"):
        StructureField(name="a", variant_type=ua.VariantType.Double, length=2)
    with pytest.raises(ValueError, match="length"):
        StructureField(name="a", variant_type=ua.VariantType.Double, array=True, length=-1)


def test_dependencies_come_first_and_are_listed_once() -> None:
    twice = StructureDefinition(
        type_name="Twice",
        fields=(
            StructureField(name="a", variant_type=ua.VariantType.ExtensionObject, datatype=OUTER),
            StructureField(name="b", variant_type=ua.VariantType.ExtensionObject, datatype=INNER),
        ),
    )
    assert [each.type_name for each in twice.dependencies()] == ["Inner", "Outer", "Twice"]


def test_node_ids_derive_from_the_type_name() -> None:
    assert structure_node_id("Sample", NAMESPACE) == ua.NodeId("Sample", NAMESPACE)
    assert encoding_node_id("Sample", NAMESPACE) == ua.NodeId("Sample.DefaultBinary", NAMESPACE)


# ---------------------------------------------------------------------------
# codec
# ---------------------------------------------------------------------------


def test_round_trip() -> None:
    values = sample()
    assert decode(SAMPLE, encode(SAMPLE, values, NAMESPACE), NAMESPACE) == values


def test_body_is_the_primitive_encodings_in_field_order() -> None:
    carried = encode(SAMPLE, sample(), NAMESPACE)
    assert carried.Body == b"".join(
        (
            Primitives.Int32.pack(1),
            Primitives.String.pack("abc"),
            Primitives.Int32.pack(2),
            Primitives.Double.pack(0.5),
            Primitives.Double.pack(1.5),
            Primitives.Int32.pack(3),
            Primitives.Double.pack(1.0),
            Primitives.Double.pack(2.0),
            Primitives.Double.pack(3.0),
            Primitives.ByteString.pack(b"\x00\x01\xff"),
            Primitives.Boolean.pack(True),
        )
    )


def test_encoded_value_carries_the_encoding_id_and_survives_the_wire() -> None:
    carried = encode(SAMPLE, sample(), NAMESPACE)
    assert carried.TypeId == encoding_node_id("Sample", NAMESPACE)
    assert carried.Encoding == 1
    received = extensionobject_from_binary(Buffer(extensionobject_to_binary(carried)))
    assert decode(SAMPLE, received, NAMESPACE) == sample()


def test_nested_structures_are_inline_without_a_header() -> None:
    values = {"names": ["a"], "inners": [{"x": 1.0, "y": 2.0}, {"x": 3.0, "y": 4.0}]}
    carried = encode(OUTER, values, NAMESPACE)
    assert carried.Body == b"".join(
        (
            Primitives.Int32.pack(1),
            Primitives.String.pack("a"),
            Primitives.Int32.pack(2),
            Primitives.Double.pack(1.0),
            Primitives.Double.pack(2.0),
            Primitives.Double.pack(3.0),
            Primitives.Double.pack(4.0),
        )
    )
    assert decode(OUTER, carried, NAMESPACE) == values


def test_empty_arrays_are_values() -> None:
    values = {"names": [], "inners": []}
    assert decode(OUTER, encode(OUTER, values, NAMESPACE), NAMESPACE) == values


def test_missing_field_is_a_key_error() -> None:
    with pytest.raises(KeyError, match="ok"):
        encode(SAMPLE, {key: value for key, value in sample().items() if key != "ok"}, NAMESPACE)


def test_fixed_size_array_of_the_wrong_length_is_refused_both_ways() -> None:
    with pytest.raises(ValueError, match=r"triple.*3 elements, and 2 were given"):
        encode(SAMPLE, {**sample(), "triple": [1.0, 2.0]}, NAMESPACE)
    spoiled = raw(
        encoding_node_id("Sample", NAMESPACE),
        sample_body(triple=Primitives.Int32.pack(2) + Primitives.Double.pack(1.0) + Primitives.Double.pack(2.0)),
    )
    with pytest.raises(ValueError, match=r"triple.*3 elements, and 2 were given"):
        decode(SAMPLE, spoiled, NAMESPACE)


def test_wrong_type_is_refused() -> None:
    with pytest.raises(ValueError, match="not an ExtensionObject"):
        decode(SAMPLE, {"id": 1}, NAMESPACE)
    foreign = raw(encoding_node_id("Other", NAMESPACE), sample_body())
    with pytest.raises(ValueError, match=r"Other\.DefaultBinary"):
        decode(SAMPLE, foreign, NAMESPACE)
    elsewhere = raw(encoding_node_id("Sample", NAMESPACE + 1), sample_body())
    with pytest.raises(ValueError, match="is not the encoding"):
        decode(SAMPLE, elsewhere, NAMESPACE)


def test_truncated_body_is_refused_at_every_cut() -> None:
    body = sample_body()
    for cut in range(len(body)):
        truncated = raw(encoding_node_id("Sample", NAMESPACE), body[:cut])
        with pytest.raises(ValueError, match=r"runs off the end|null"):
            decode(SAMPLE, truncated, NAMESPACE)


def test_trailing_bytes_are_refused() -> None:
    long = raw(encoding_node_id("Sample", NAMESPACE), sample_body() + b"\x00\x00")
    with pytest.raises(ValueError, match="2 bytes more"):
        decode(SAMPLE, long, NAMESPACE)


def test_null_array_and_null_string_are_refused() -> None:
    null_array = raw(encoding_node_id("Sample", NAMESPACE), sample_body(readings=Primitives.Int32.pack(-1)))
    with pytest.raises(ValueError, match="null array"):
        decode(SAMPLE, null_array, NAMESPACE)
    null_string = raw(
        encoding_node_id("Sample", NAMESPACE),
        Primitives.Int32.pack(1) + Primitives.Int32.pack(-1) + sample_body()[8 + 3 :],
    )
    with pytest.raises(ValueError, match=r"label.*null"):
        decode(SAMPLE, null_string, NAMESPACE)


def test_a_field_type_without_a_packer_is_refused() -> None:
    odd = StructureDefinition(type_name="Odd", fields=(StructureField(name="n", variant_type=ua.VariantType.NodeId),))
    with pytest.raises(ValueError, match="NodeId"):
        encode(odd, {"n": ua.NodeId(1, 0)}, NAMESPACE)


# ---------------------------------------------------------------------------
# write check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_write_check_accepts_arrays_of_the_structure() -> None:
    var = UaVariable(
        initial_value=[encode(SAMPLE, sample(), NAMESPACE)],
        write_check_callback=structure_array_write_check(SAMPLE, NAMESPACE),
    )
    await var.write_check([])
    await var.write_check([encode(SAMPLE, sample(1), NAMESPACE), encode(SAMPLE, sample(2), NAMESPACE)])


@pytest.mark.asyncio
async def test_write_check_refuses_with_bad_type_mismatch() -> None:
    var = UaVariable(
        initial_value=[encode(SAMPLE, sample(), NAMESPACE)],
        write_check_callback=structure_array_write_check(SAMPLE, NAMESPACE),
    )
    other = encode(INNER, {"x": 1.0, "y": 2.0}, NAMESPACE)
    truncated = raw(encoding_node_id("Sample", NAMESPACE), sample_body()[:5])
    long = raw(encoding_node_id("Sample", NAMESPACE), sample_body() + b"\x00")
    for value in (1.0, "text", encode(SAMPLE, sample(), NAMESPACE), [1.0], [other], [truncated], [long]):
        with pytest.raises(ua.UaStatusCodeError) as refused:
            await var.write_check(value)  # pyright: ignore[reportArgumentType]
        assert refused.value.code == StatusCodes.BadTypeMismatch
