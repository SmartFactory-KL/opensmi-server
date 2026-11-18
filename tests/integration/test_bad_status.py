# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from dataclasses import dataclass, field

import pytest
from asyncua import ua
from asyncua.client.client import Client

from opensmi.server import BaseMachine, ParameterSet, UaVariable
from opensmi.server.mixins import ParameterSetMixin

BAD = ua.StatusCode(ua.StatusCodes.BadNoCommunication)


@dataclass
class Size:
    length: float = 0.0
    width: float = 0.0


@dataclass
class Part:
    id: str = ""
    size: Size = field(default_factory=Size)


class Parameters(ParameterSet):
    Speed = UaVariable(initial_value=1.0, writable=True, bypass_lock=True, range=(0.0, 10.0), unit="m/s")
    Current = UaVariable(initial_value=Part("P-1", Size(120.0, 80.0)), writable=True, bypass_lock=True)
    Locked = UaVariable(initial_value=1.0, writable=True)


class StatusMachine(ParameterSetMixin[Parameters], BaseMachine):
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("1")
        await self.identification.ProductInstanceUri.write("urn:example:1")
        await self.identification.Manufacturer.write(ua.LocalizedText("Example", "en"))


def url(server) -> str:
    return f"opc.tcp://operator:operator@localhost:{server.ua_server.bserver.port}"


async def status_and_value(variable: UaVariable) -> tuple[int, object]:
    data_value = await variable.ua_node.read_data_value(raise_on_bad_status=False)
    return data_value.StatusCode.value, data_value.Value.Value


@pytest.mark.asyncio
async def test_a_bad_status_without_a_value_is_accepted_by_a_variable_that_is_not_optional(empty_server) -> None:
    speed = (await empty_server.add_machine(StatusMachine())).parameter_set.Speed
    assert speed.optional_ok is False

    await speed.write_status(ua.StatusCodes.BadNoCommunication)
    assert await status_and_value(speed) == (ua.StatusCodes.BadNoCommunication, None)

    await speed.write(2.0)
    assert await status_and_value(speed) == (ua.StatusCodes.Good, 2.0)


@pytest.mark.asyncio
async def test_a_good_status_without_a_value_is_still_refused(empty_server) -> None:
    speed = (await empty_server.add_machine(StatusMachine())).parameter_set.Speed

    with pytest.raises(ua.UaStatusCodeError):
        await speed.ua_node.write_value(ua.DataValue(Value=ua.Variant()))

    assert await speed.read() == 1.0


@pytest.mark.asyncio
async def test_a_bad_status_from_a_client_still_needs_the_lock(empty_server) -> None:
    locked = (await empty_server.add_machine(StatusMachine())).parameter_set.Locked

    async with Client(url(empty_server)) as client:
        node = client.get_node(locked.ua_node.nodeid)
        with pytest.raises(ua.UaStatusCodeError) as refused:
            await node.write_value(ua.DataValue(StatusCode=BAD))

    assert refused.value.code == ua.StatusCodes.BadUserAccessDenied
    assert await status_and_value(locked) == (ua.StatusCodes.Good, 1.0)


@pytest.mark.asyncio
async def test_a_bad_status_on_a_complex_variable_without_optional_ok_reaches_every_node(empty_server) -> None:
    current = (await empty_server.add_machine(StatusMachine())).parameter_set.Current

    await current.ua_node.write_value(ua.DataValue(StatusCode=BAD))

    for node in (current, current["Id"], current["Size"], current["Size"]["Length"]):
        assert await status_and_value(node) == (ua.StatusCodes.BadNoCommunication, None), node.path


@pytest.mark.asyncio
async def test_a_bad_status_on_a_leaf_reaches_the_whole_complex_variable(empty_server) -> None:
    current = (await empty_server.add_machine(StatusMachine())).parameter_set.Current

    await current["Size"]["Length"].write_status(BAD)

    for node in (current, current["Id"], current["Size"], current["Size"]["Width"], current["Size"]["Length"]):
        assert await status_and_value(node) == (ua.StatusCodes.BadNoCommunication, None), node.path

    # nothing to merge a leaf into until the whole structure is written again
    with pytest.raises(ua.UaStatusCodeError):
        await current["Size"]["Length"].write(5.0)
    await current.write(Part("P-2", Size(5.0, 6.0)))
    assert await current["Size"]["Length"].read() == 5.0


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [ua.StatusCodes.Good, ua.StatusCodes.UncertainLastUsableValue])
async def test_write_status_refuses_a_status_that_is_not_bad(empty_server, status) -> None:
    speed = (await empty_server.add_machine(StatusMachine())).parameter_set.Speed

    with pytest.raises(ValueError, match="not a bad status"):
        await speed.write_status(status)

    assert await status_and_value(speed) == (ua.StatusCodes.Good, 1.0)
