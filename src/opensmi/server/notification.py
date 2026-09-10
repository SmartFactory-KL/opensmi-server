# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Interface and implementation for OPC UA based logging.

- There is always at least one actual OPC UA logger for the machine (`BaseMachine`).
- Skills (`BaseSkill`) and Methods (`BaseMethod`) never have their own logger and forward them to their parent.
- Components (`BaseMachineryItem`) can optionally have their own or forward them to their parent.
"""

from collections.abc import AsyncGenerator

from asyncua import ua
from asyncua.server.event_generator import EventGenerator
from opensmi.core.lifecycle_mixin import lifecycle
from typing_extensions import override

from opensmi.server.base_machinery_item import BaseMachineryItem
from opensmi.server.interfaces import AbstractUaLogger, AbstractUaObject
from opensmi.server.mixins.parent_mixin import ParentMixin
from opensmi.server.nodesets import SmartFactoryMachineSetNodeIds
from opensmi.server.ua_object import UaObject, UaObjectDefinition


class Notification(UaObject, ParentMixin[BaseMachineryItem], AbstractUaLogger):
    """Implementation of the OPC UA Logging interface for `BaseMachine` and optionally `BaseMachineryItem`."""

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
        # setup event generators
        self._ua_info_event_gen = await self.server.ua_server.get_event_generator(
            etype=ua.object_ids.ObjectIds.BaseEventType,
            emitting_node=self.ua_node.nodeid,  # type: ignore
        )
        self._ua_alarm_event_gen = await self.server.ua_server.get_event_generator(
            etype=self.server.ua_get_node_id(SmartFactoryMachineSetNodeIds.AlertType),
            emitting_node=self.ua_node.nodeid,  # type: ignore
        )

        # TODO(CaHa): Fix/Implement? event historizing
        # await self.server.ua_server.historize_node_event(self.ua_node, count=1000)

        yield
        # no shutdown

    async def _generate_info_event(
        self, *, msg: str, severity: int = 0, source: AbstractUaObject | None = None
    ) -> None:
        self.logger.debug("Generating info event", msg=msg, severity=severity, source=source)

        # TODO(CaHa): Not properly supported in asyncua
        # if source is None:
        #     self._ua_info_event_gen.event.SourceNode = self.parent.ua_node.nodeid
        #     self._ua_info_event_gen.event.SourceName = self.parent.name
        # else:
        #     self._ua_info_event_gen.event.SourceNode = source.ua_node.nodeid
        #     self._ua_info_event_gen.event.SourceName = source.name

        self._ua_info_event_gen.event.Severity = severity
        self._ua_info_event_gen.event.Message = ua.LocalizedText(msg)
        await self._ua_info_event_gen.trigger()

    async def _generate_alert_event(
        self, *, msg: str, severity: int = 0, code: str = "", source: AbstractUaObject | None = None
    ) -> None:
        self.logger.debug("Generating alert event", msg=msg, severity=severity, code=code, source=source)

        # TODO(CaHa): Not properly supported in asyncua
        # if source is None:
        #     self._ua_alarm_event_gen.event.SourceNode = self.parent.ua_node.nodeid
        #     self._ua_alarm_event_gen.event.SourceName = self.parent.name
        # else:
        #     self._ua_alarm_event_gen.event.SourceNode = source.ua_node.nodeid
        #     self._ua_alarm_event_gen.event.SourceName = source.name

        self._ua_alarm_event_gen.event.Severity = severity
        # TODO(CaHa): remove "|||" in message work-around when no longer required (asyncua client problem)
        self._ua_alarm_event_gen.event.Message = ua.LocalizedText(f"{msg}|||{code}")
        self._ua_alarm_event_gen.event.ErrorCode = code
        await self._ua_alarm_event_gen.trigger()

    @override
    async def ua_log_info(self, msg: str, *, severity: int = 0, source: AbstractUaObject | None = None) -> None:
        await self._generate_info_event(msg=msg, severity=severity, source=source)

    @override
    async def ua_log_warning(self, msg: str, *, severity: int = 555, source: AbstractUaObject | None = None) -> None:
        await self._generate_info_event(msg=msg, severity=severity, source=source)

    @override
    async def ua_log_error(
        self, msg: str, *, severity: int = 999, code: str = "", source: AbstractUaObject | None = None
    ) -> None:
        await self._generate_alert_event(msg=msg, severity=severity, code=code, source=source)
