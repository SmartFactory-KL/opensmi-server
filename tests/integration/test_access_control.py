# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio

import pytest
from asyncua.client.client import Client
from asyncua.ua.uaerrors import BadIdentityTokenRejected, BadUserAccessDenied


@pytest.mark.asyncio
async def test_deny_anonymous_access(machine) -> None:
    with pytest.raises(BadIdentityTokenRejected):
        await Client(url=f"opc.tcp://localhost:{machine.server.ua_server.bserver.port}").connect()


@pytest.mark.asyncio
async def test_deny_invalid_user(machine) -> None:
    with pytest.raises(BadUserAccessDenied):
        await Client(url=f"opc.tcp://invalid:123@localhost:{machine.server.ua_server.bserver.port}").connect()


@pytest.mark.asyncio
@pytest.mark.parametrize(("user", "password"), [("orchestrator", "orchestrator"), ("operator", "operator")])
async def test_login_limitation(machine, user, password) -> None:
    machine.server.config.access_control.allow_reconnection_from_same_host = False

    async with Client(url=f"opc.tcp://{user}:{password}@localhost:{machine.server.ua_server.bserver.port}"):
        client_2nd = Client(url=f"opc.tcp://{user}:{password}@localhost:{machine.server.ua_server.bserver.port}")
        with pytest.raises(BadIdentityTokenRejected):
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
