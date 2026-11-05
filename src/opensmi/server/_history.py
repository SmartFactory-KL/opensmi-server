# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Workarounds for bugs in asyncua's server-side history (checked against asyncua 2.1.0)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any

import aiosqlite
from asyncua import ua
from asyncua.common.events import Event
from asyncua.common.sql_injection import validate_table_name
from asyncua.server.history import HistoryManager, HistoryStorageInterface, SubHandler
from asyncua.server.history_sql import HistorySQLite
from typing_extensions import override

if TYPE_CHECKING:
    from asyncua.common.node import Node
    from asyncua.common.subscription import Subscription, SubscriptionHandler
    from asyncua.server.internal_server import InternalServer


class FixedHistorySQLite(HistorySQLite):
    """`HistorySQLite` with working event history (see the comments of the overridden methods)."""

    @override
    def _get_table_name(self, node_id: ua.NodeId) -> str:
        if node_id.NodeIdType == ua.NodeIdType.String:
            return f"{node_id.NamespaceIndex}_{node_id.Identifier}"  # repr creates invalid ' character
        return f"{node_id.NamespaceIndex}_{node_id.Identifier!r}"

    @override
    async def _get_event_fields(self, evtypes: list[Node]) -> list[str]:
        # asyncua de-duplicates the property *nodes*, but subtypes may redefine a property with the same name
        # (e.g. ``ConditionClassId`` in alarm types); duplicate column names make ``CREATE TABLE`` fail.
        # See https://github.com/FreeOpcUa/opcua-asyncio/issues/2076.
        return list(dict.fromkeys(await super()._get_event_fields(evtypes)))

    @override
    async def save_event(self, event: Event) -> None:
        # Re-implemented because asyncua's version
        # - inserts into the table of ``event.SourceNode``, but creates and reads the table of the historized
        #   (emitting) node, so events with a different ``SourceNode`` are lost
        #   (https://github.com/FreeOpcUa/opcua-asyncio/issues/2075);
        # - inserts all event fields, but the table columns are only the *properties* of the event types; events of
        #   condition/alarm types also carry *variables* (e.g. ``AckedState/Id``) without a column, so the INSERT fails
        #   (https://github.com/FreeOpcUa/opcua-asyncio/issues/2077);
        # - deletes old events by comparing the binary ``Time`` column with a text timestamp, which never matches.
        source = event.emitting_node
        columns = self._event_fields.get(source)
        if columns is not None:
            fields = event.get_event_props_as_fields_dict()
            event = Event.from_field_dict({name: fields[name] for name in columns if name in fields})
        table = self._get_table_name(source)
        validate_table_name(table)
        names, placeholders, values = self._format_event(event)
        timestamp: Any = getattr(event, "Time", None)
        event_type: Any = getattr(event, "EventType", None)
        try:
            await self._db.execute(
                f'INSERT INTO "{table}" ("_Id", "_Timestamp", "_EventTypeName", {names}) '
                f"VALUES (NULL, ?, ?, {placeholders})",
                (str(timestamp), str(event_type), *values),
            )
            await self._db.commit()
        except aiosqlite.Error:
            self.logger.exception("Historizing SQL Insert Error for events from %s", source)
        period = self._datachanges_period.get(source)
        if period:
            date_limit = datetime.now(UTC) - period
            try:
                await self._db.execute(f'DELETE FROM "{table}" WHERE "_Timestamp" < ?', (str(date_limit),))
                await self._db.commit()
            except aiosqlite.Error:
                self.logger.exception("Historizing SQL Delete Old Data Error for events from %s", source)


class _EventSourceSubHandler(SubHandler):
    """`SubHandler` that restores ``Event.emitting_node`` before the event is stored.

    asyncua's history subscription rebuilds each event with ``Event.from_event_fields``, which leaves
    ``emitting_node`` at its default (the Server object, ``i=2253``). The storages key the history
    by ``emitting_node``, so ``save_event`` raises ``KeyError`` and nothing can be read back.
    See https://github.com/FreeOpcUa/opcua-asyncio/issues/2074.
    """

    def __init__(self, storage: HistoryStorageInterface, event_sources: dict[int, ua.NodeId]) -> None:
        super().__init__(storage)
        self._event_sources: dict[int, ua.NodeId] = event_sources

    @override
    def event_notification(self, event: Event) -> None:
        server_handle: int | None = getattr(event, "server_handle", None)
        source = self._event_sources.get(server_handle) if server_handle is not None else None
        if source is not None:
            event.emitting_node = source
        super().event_notification(event)


class FixedHistoryManager(HistoryManager):
    """`HistoryManager` whose event history actually stores events under the historized node.

    Workaround for https://github.com/FreeOpcUa/opcua-asyncio/issues/2074.
    """

    def __init__(self, iserver: InternalServer) -> None:
        super().__init__(iserver)
        self._event_sources: dict[int, ua.NodeId] = {}  # monitored item server handle -> historized node

    @override
    async def _create_subscription(self, handler: SubscriptionHandler) -> Subscription:
        return await super()._create_subscription(_EventSourceSubHandler(self.storage, self._event_sources))

    @override
    async def historize_event(self, source: Node, period: timedelta | None = timedelta(days=7), count: int = 0) -> None:
        await super().historize_event(source, period, count)
        handle: Any = self._handlers[source]
        self._event_sources[handle] = source.nodeid

    @override
    async def dehistorize(self, node: Node) -> None:
        handle: Any = self._handlers.get(node)
        await super().dehistorize(node)
        if handle is not None:
            self._event_sources.pop(handle, None)
