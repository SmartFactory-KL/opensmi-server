# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio
from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from asyncua import ua
from asyncua.client.client import Client
from asyncua.ua.uaerrors import BadIdentityTokenRejected, BadLocked, BadUserAccessDenied
from typing_extensions import override

from opensmi.server import (
    BaseMachine,
)


class Machine(BaseMachine):
    @override
    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )


@pytest_asyncio.fixture
async def machine(server) -> AsyncGenerator[Machine]:
    machine = Machine()
    async with server:
        await server.add_machine(machine)
        await server.start(blocking=False)
        yield machine


@pytest.mark.asyncio
async def test_deny_anonymous_access(machine) -> None:
    with pytest.raises(BadIdentityTokenRejected):
        await Client(url=f"opc.tcp://localhost:{machine.server.ua_server.bserver.port}").connect()


@pytest.mark.asyncio
async def test_deny_invalid_user(machine) -> None:
    with pytest.raises(BadUserAccessDenied):
        await Client(url=f"opc.tcp://invalid:123@localhost:{machine.server.ua_server.bserver.port}").connect()


@pytest.mark.asyncio
async def test_deny_visitor_access(machine) -> None:
    async with Client(url=f"opc.tcp://visitor:visitor@localhost:{machine.server.ua_server.bserver.port}") as client:
        assert not machine.lock.locking_user

        client_lock_node = client.get_node(machine.lock.ua_node.nodeid)

        # try every method of AccessControl
        with pytest.raises(BadUserAccessDenied):
            await client_lock_node.call_method("2:InitLock")
        with pytest.raises(BadUserAccessDenied):
            await client_lock_node.call_method("2:ExitLock")
        with pytest.raises(BadUserAccessDenied):
            await client_lock_node.call_method("2:BreakLock")
        with pytest.raises(BadUserAccessDenied):
            await client_lock_node.call_method("2:RenewLock")


@pytest.mark.asyncio
@pytest.mark.parametrize(("user", "password"), [("orchestrator", "orchestrator"), ("operator", "operator")])
async def test_login_limitation(machine, user, password) -> None:
    machine.server.config.access_control.allow_reconnection_from_same_host = False

    async with Client(url=f"opc.tcp://{user}:{password}@localhost:{machine.server.ua_server.bserver.port}"):
        client_2nd = Client(url=f"opc.tcp://{user}:{password}@localhost:{machine.server.ua_server.bserver.port}")
        with pytest.raises(BadUserAccessDenied):
            # should fail, because only one is allowed to log in at a time
            await client_2nd.connect()


@pytest.mark.asyncio
@pytest.mark.parametrize(("user", "password"), [("orchestrator", "orchestrator"), ("operator", "operator")])
async def test_login_limitation_allow_reconnection_from_same_host(machine, user, password) -> None:
    assert len(machine.server.access_control.sessions) == 0
    machine.server.config.access_control.allow_reconnection_from_same_host = True

    async with Client(url=f"opc.tcp://{user}:{password}@localhost:{machine.server.ua_server.bserver.port}"):
        assert len(machine.server.access_control.sessions) == 1
        client_2nd = Client(url=f"opc.tcp://{user}:{password}@localhost:{machine.server.ua_server.bserver.port}")
        # should not fail, even though only one is allowed to log in at a time, reconnection from the same host is allowed
        await client_2nd.connect()

        # not instant, should be more than long enough...
        async with asyncio.timeout(5):
            while len(machine.server.access_control.sessions) != 1:  # noqa: ASYNC110
                await asyncio.sleep(0.1)

        await client_2nd.disconnect()


@pytest.mark.asyncio
async def test_operator_locking(machine) -> None:
    client = Client(url=f"opc.tcp://operator:operator@localhost:{machine.server.ua_server.bserver.port}")
    async with client:
        assert not machine.lock.locking_user

        client_lock_node = client.get_node(machine.lock.ua_node.nodeid)

        await client_lock_node.call_method("2:InitLock")
        assert machine.lock.locking_user
        assert machine.lock.locking_user.name == "operator"

        await client_lock_node.call_method("2:ExitLock")
        assert not machine.lock.locking_user


@pytest.mark.asyncio
async def test_auto_unlock(machine) -> None:
    assert len(machine.server.access_control.sessions) == 0
    client = Client(url=f"opc.tcp://operator:operator@localhost:{machine.server.ua_server.bserver.port}")
    async with client:
        assert not machine.lock.locking_user
        assert len(machine.server.access_control.sessions) == 1

        await client.get_node(machine.lock.ua_node.nodeid).call_method("2:InitLock")
        assert machine.lock.locking_user
        assert machine.lock.locking_user.name == "operator"

    # not instant, should be more than long enough...
    async with asyncio.timeout(5):
        while not machine.lock.locking_user:  # noqa: ASYNC110
            await asyncio.sleep(0.1)
        while len(machine.server.access_control.sessions) != 0:  # noqa: ASYNC110
            await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_operator_can_break_orchestrator_owned_lock(machine) -> None:
    async with Client(
        url=f"opc.tcp://orchestrator:orchestrator@localhost:{machine.server.ua_server.bserver.port}"
    ) as client_orchestrator:
        assert not machine.lock.locking_user

        await client_orchestrator.get_node(machine.lock.ua_node.nodeid).call_method("2:InitLock")
        assert machine.lock.locking_user
        assert machine.lock.locking_user.name == "orchestrator"

        async with Client(
            url=f"opc.tcp://operator:operator@localhost:{machine.server.ua_server.bserver.port}"
        ) as client_operator:
            with pytest.raises(BadLocked):
                await client_operator.get_node(machine.lock.ua_node.nodeid).call_method("2:InitLock")

            # kick orchestrator out
            await client_operator.get_node(machine.lock.ua_node.nodeid).call_method("2:BreakLock")
            assert machine.lock.locking_user.name == "operator"


@pytest.mark.asyncio
async def test_orchestrator_cannot_break_operator_owned_lock(machine) -> None:
    async with Client(
        url=f"opc.tcp://operator:operator@localhost:{machine.server.ua_server.bserver.port}"
    ) as client_operator:
        assert not machine.lock.locking_user
        await client_operator.get_node(machine.lock.ua_node.nodeid).call_method("2:InitLock")
        assert machine.lock.locking_user
        assert machine.lock.locking_user.name == "operator"

        async with Client(
            url=f"opc.tcp://orchestrator:orchestrator@localhost:{machine.server.ua_server.bserver.port}"
        ) as client_orchestrator:
            with pytest.raises(BadLocked):
                await client_orchestrator.get_node(machine.lock.ua_node.nodeid).call_method("2:InitLock")
            with pytest.raises(BadUserAccessDenied):
                await client_orchestrator.get_node(machine.lock.ua_node.nodeid).call_method("2:BreakLock")
