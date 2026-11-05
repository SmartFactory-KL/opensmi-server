# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from asyncua.client.client import Client

from opensmi.server import BaseMachine, Server
from opensmi.server.config import HistoryOption, ServerConfiguration


@pytest.fixture(params=[HistoryOption.MEMORY, HistoryOption.SQLITE], ids=["memory", "sqlite"])
def server(request, tmp_path, monkeypatch) -> Server:
    """Override of the conftest fixture: an uninitialized `Server` with each history storage."""
    monkeypatch.chdir(tmp_path)  # the SQLite history database is created relative to the working directory
    config = ServerConfiguration()
    config.ua_server.endpoint_address = "opc.tcp://0.0.0.0:0/server"
    config.ua_server.history_db = request.param
    return Server(config=config)


@pytest.mark.asyncio
async def test_notification_event_history(machine: BaseMachine) -> None:
    start = datetime.now(UTC) - timedelta(seconds=1)
    messages = ["info event", "warning event", "error event"]
    await machine.ua_log_info(messages[0])
    await machine.ua_log_warning(messages[1])
    await machine.ua_log_error(messages[2], code="E42")

    port = machine.server.ua_server.bserver.port
    async with Client(url=f"opc.tcp://visitor:visitor@localhost:{port}") as client:
        node = client.get_node(machine.notification.ua_node.nodeid)
        read: list[str] = []
        async with asyncio.timeout(5):  # events are stored asynchronously
            while not set(messages) <= set(read):
                events = await node.read_event_history(start, datetime.now(UTC) + timedelta(seconds=1))
                read = [event.Message.Text for event in events]  # pyright: ignore[reportAttributeAccessIssue]
                await asyncio.sleep(0.05)

    assert [m for m in read if m in messages] == messages
