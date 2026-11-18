# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Implementation of the `AbstractUaLogger` interface with a Notification OPC UA object and event sources."""

from collections.abc import AsyncGenerator
from datetime import timedelta
from typing import TYPE_CHECKING, Final

from asyncua import ua
from asyncua.common.node import Node
from asyncua.server.event_generator import EventGenerator
from opensmi.core.lifecycle_mixin import lifecycle
from opensmi.core.nodesets import SmartFactoryMachineSetNodeIds
from typing_extensions import override

from opensmi.server.interfaces import AbstractUaLogger, AbstractUaObject
from opensmi.server.mixins.parent_mixin import ParentMixin
from opensmi.server.ua_object import UaObject, UaObjectDefinition

if TYPE_CHECKING:
    from opensmi.server.base_machinery_item import BaseMachineryItem as BaseMachineryItem


class Notification(ParentMixin["BaseMachineryItem"], UaObject, AbstractUaLogger):
    """Implementation of the `AbstractUaLogger` interface with a Notification OPC UA object and event sources.

    The events it emits are kept in the server's event history, see ``docs/events.md``.
    """

    HISTORY_EVENT_COUNT: Final = 1000
    """Most events kept in the history of a `Notification`; asyncua's SQLite history storage ignores it."""
    HISTORY_EVENT_PERIOD: Final = timedelta(days=7)
    """How long events are kept in the history of a `Notification`."""

    _ua_info_event_gen: EventGenerator
    _ua_alarm_event_gen: EventGenerator

    def __init__(self) -> None:
        """*Cooperative* constructor."""
        super().__init__(name="Notification")

    @override
    async def _get_definition(self) -> UaObjectDefinition:
        return UaObjectDefinition(
            object_type=SmartFactoryMachineSetNodeIds.NotificationType,
            namespace_uri=SmartFactoryMachineSetNodeIds,
        )

    @lifecycle
    async def lifecycle_notification(self) -> AsyncGenerator[None]:
        """Create the event generators for info and alert events."""
        # setup event generators
        self._ua_info_event_gen = await self.server.ua_server.get_event_generator(
            etype=ua.object_ids.ObjectIds.BaseEventType,
            emitting_node=self.ua_node.nodeid,  # type: ignore
        )
        self._ua_alarm_event_gen = await self.server.ua_server.get_event_generator(
            etype=self.server.ua_get_node_id(SmartFactoryMachineSetNodeIds.AlertType),
            emitting_node=self.ua_node.nodeid,  # type: ignore
        )

        # historize the events of both generators (must happen after they added their GeneratesEvent references)
        await self._historize_events()

        yield
        # no shutdown

    async def add_event_generator(self, event_type: Node | ua.NodeId | int) -> EventGenerator:
        """Return a generator for events of `event_type` emitted by this `Notification`, kept in its event history.

        Use it instead of ``ua_server.get_event_generator()`` for event types beyond the info and alert events, so the
        history also stores the fields of `event_type`. Call it during setup, e.g. in a `lifecycle` step ordered after
        `lifecycle_notification`: events emitted shortly before or while it runs can be missing from the history.
        """
        generator = await self.server.ua_server.get_event_generator(etype=event_type, emitting_node=self.ua_node.nodeid)
        # historize again: the history only stores the fields of the event types generated when it is set up
        await self._historize_events()
        return generator

    async def _historize_events(self) -> None:
        await self.server.ua_server.historize_node_event(
            self.ua_node, period=self.HISTORY_EVENT_PERIOD, count=self.HISTORY_EVENT_COUNT
        )

    async def _generate_info_event(
        self, *, msg: str, severity: int = 0, source: AbstractUaObject | None = None
    ) -> None:
        self.logger.debug("Generating info event", msg=msg, severity=severity, source=source)

        source_object = self.parent if source is None else source
        self._ua_info_event_gen.event.SourceNode = source_object.ua_node.nodeid
        self._ua_info_event_gen.event.SourceName = source_object.name

        self._ua_info_event_gen.event.Severity = severity
        self._ua_info_event_gen.event.Message = ua.LocalizedText(msg)
        await self._ua_info_event_gen.trigger()

    async def _generate_alert_event(
        self, *, msg: str, severity: int = 0, code: str = "", source: AbstractUaObject | None = None
    ) -> None:
        self.logger.debug("Generating alert event", msg=msg, severity=severity, code=code, source=source)

        source_object = self.parent if source is None else source
        self._ua_alarm_event_gen.event.SourceNode = source_object.ua_node.nodeid
        self._ua_alarm_event_gen.event.SourceName = source_object.name

        self._ua_alarm_event_gen.event.Severity = severity
        self._ua_alarm_event_gen.event.Message = ua.LocalizedText(msg)
        self._ua_alarm_event_gen.event.ErrorCode = code
        await self._ua_alarm_event_gen.trigger()

    @override
    async def ua_log_info(self, msg: str, *, severity: int = 0, source: AbstractUaObject | None = None) -> None:
        self.logger.info("UA Log", msg=msg, severity=severity, source=source)
        await self._generate_info_event(msg=msg, severity=severity, source=source)

    @override
    async def ua_log_warning(self, msg: str, *, severity: int = 555, source: AbstractUaObject | None = None) -> None:
        self.logger.warning("UA Log", msg=msg, severity=severity, source=source)
        await self._generate_info_event(msg=msg, severity=severity, source=source)

    @override
    async def ua_log_error(
        self, msg: str, *, severity: int = 999, code: str = "", source: AbstractUaObject | None = None
    ) -> None:
        self.logger.error("UA Log", msg=msg, severity=severity, code=code, source=source)
        await self._generate_alert_event(msg=msg, severity=severity, code=code, source=source)
