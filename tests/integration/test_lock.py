# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio

import pytest
from asyncua.client.client import Client
from asyncua.ua.uaerrors import BadLocked, BadUserAccessDenied


@pytest.mark.asyncio
async def test_deny_visitor_locking(machine) -> None:
    async with Client(url=f"opc.tcp://visitor:visitor@localhost:{machine.server.ua_server.bserver.port}") as client:
        assert not machine.lock.locking_user

        client_lock_node = client.get_node(machine.lock.ua_node.nodeid)

        # try every method of Lock
        with pytest.raises(BadUserAccessDenied):
            await client_lock_node.call_method("2:InitLock")
        with pytest.raises(BadUserAccessDenied):
            await client_lock_node.call_method("2:ExitLock")
        with pytest.raises(BadUserAccessDenied):
            await client_lock_node.call_method("2:BreakLock")
        with pytest.raises(BadUserAccessDenied):
            await client_lock_node.call_method("2:RenewLock")


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


@pytest.mark.asyncio
async def test_lock_timeout(machine) -> None:
    machine.server.config.access_control.max_inactive_lock_time_milliseconds = 1000
    client = Client(url=f"opc.tcp://operator:operator@localhost:{machine.server.ua_server.bserver.port}")
    async with client:
        assert not machine.lock.locking_user

        await client.get_node(machine.lock.ua_node.nodeid).call_method("2:InitLock")
        assert machine.lock.locking_user
        assert machine.lock.locking_user.name == "operator"

        # not instant, should be more than long enough...
        async with asyncio.timeout(5):
            while machine.lock.locking_user:  # noqa: ASYNC110
                await asyncio.sleep(0.1)


@pytest.mark.asyncio
async def test_lock_renew(machine) -> None:
    client = Client(url=f"opc.tcp://operator:operator@localhost:{machine.server.ua_server.bserver.port}")
    async with client:
        assert not machine.lock.locking_user
        assert machine.lock.remaining_lock_time_ms == 0

        await client.get_node(machine.lock.ua_node.nodeid).call_method("2:InitLock")
        assert machine.lock.locking_user
        assert machine.lock.locking_user.name == "operator"

        start = machine.lock.remaining_lock_time_ms
        assert start > 0
        # wait until it gets updated (should happen easily within 5 seconds)
        async with asyncio.timeout(5):
            while machine.lock.remaining_lock_time_ms == start:  # noqa: ASYNC110
                await asyncio.sleep(0.1)

        before = machine.lock.remaining_lock_time_ms
        await client.get_node(machine.lock.ua_node.nodeid).call_method("2:RenewLock")
        assert machine.lock.locking_user
        assert machine.lock.locking_user.name == "operator"
        assert machine.lock.remaining_lock_time_ms > before
