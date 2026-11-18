# Events

Machines, and components that have their own `Notification`, report messages as OPC UA events. Clients receive them by
subscribing to the events of the `Notification` object, and can read past events from its event history.

## Notifications

A `Notification` is a child object of a machinery item. Machines always have one (via `NotificationMixin`); components
either have their own (via `NotificationMixin`) or forward their messages to their parent (via
`NotificationForwarderMixin`), as do skills and methods.

Log a message with one of these methods of the machinery item, skill or method:

| Method             | Event type      | Default `Severity` |
|--------------------|-----------------|--------------------|
| `ua_log_info()`    | `BaseEventType` | 0                  |
| `ua_log_warning()` | `BaseEventType` | 555                |
| `ua_log_error()`   | `AlertType`     | 999                |

`ua_log_error()` also takes a `code`, sent in the `ErrorCode` field of the event.

Every event is emitted by the `Notification` object. `SourceNode` and `SourceName` name the object the message is
about: the `source` argument if given, else the machinery item the `Notification` belongs to.

## Event history

The server keeps the events of every `Notification` in its event history, which clients read with `HistoryRead`
(e.g. `node.read_event_history()` in asyncua). How many events it keeps depends on the history storage, set by
`history_db` in the `[ua_server]` section of the server configuration:

| `history_db`         | Kept events                                                                                  |
|----------------------|----------------------------------------------------------------------------------------------|
| `"memory"` (default) | The last 1000 events, none older than 7 days. Lost when the server stops.                    |
| `"sqlite"`           | All events of the last 7 days, in `data/opcua_history.sql` below the working directory.      |

The storages differ because asyncua implements them differently: its in-memory storage applies both the count and the
period, its SQLite storage only the period. The limits are `Notification.HISTORY_EVENT_COUNT` and
`Notification.HISTORY_EVENT_PERIOD`.

## Advanced: own event types

Most machines only need `ua_log_info()`, `ua_log_warning()` and `ua_log_error()`. This section is for integrations that
define their own event types in a NodeSet and emit them from a `Notification`.

Create the generator for such an event type with `Notification.add_event_generator()`, not with
`ua_server.get_event_generator()`:

```python
from collections.abc import AsyncGenerator

from asyncua.server.event_generator import EventGenerator
from opensmi.core.lifecycle_mixin import lifecycle

from opensmi.server import BaseMachine
from opensmi.server.mixins import NotificationMixin


class MyMachine(BaseMachine):
    _my_events: EventGenerator

    @lifecycle(after=NotificationMixin.lifecycle_notification)
    async def lifecycle_my_events(self) -> AsyncGenerator[None]:
        self._my_events = await self.notification.add_event_generator(self.server.ua_get_node_id(MY_EVENT_TYPE))
        yield
```

The event history only stores the fields of the event types the `Notification` generates when its history is set up.
`add_event_generator()` sets it up again, so the fields of the new event type are stored as well. A generator created
with `ua_server.get_event_generator()` emits its events, but their fields specific to the new type are missing from
the history.

Add event types during setup, before the machine emits events: events emitted shortly before or while
`add_event_generator()` runs can be missing from the history.
