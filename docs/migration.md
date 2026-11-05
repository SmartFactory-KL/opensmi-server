# Migration

This guide helps you upgrade to the newest version of OpenSMI-Server. Sections are ordered newest first. If you skip
versions, apply every section between your version and the newest one, oldest first.

- [OpenSMI v0.2 -> v0.3 (unreleased)](#opensmi-v02---v03-unreleased)
- [OpenSMI v0.1 -> v0.2](#opensmi-v01---v02)

## OpenSMI v0.2 -> v0.3 (unreleased)

### Dependencies

- OpenSMI-Server requires `OpenSMI-Core>=0.7,<0.8`.
- Clients must handle the new server-wide users folder, the `ErrorCode` event field and the new `SourceNode` of
  notification events, see [Users](#users-are-server-wide),
  [Notifications](#notifications-error-code-no-longer-in-the-message) and
  [`SourceNode`](#notifications-sourcenode-is-the-object-the-message-is-about). Released OpenSMI-Client versions up to
  now do not show users and drop notifications.

### Server configuration

- `Server(config_path=...)` is now `Server(config=...)`. `config` accepts a path to a TOML file or a
  `ServerConfiguration` instance.
- `Server.load_config()` was removed. Pass the config to `__init__()` or assign `server.config` before the server is
  initialized.
- `AccessControl(server=...)` is now `AccessControl(config=...)` with an `AccessControlConfiguration`. You only need
  this if you construct `AccessControl` yourself, the server does it for you.

### Node sets moved to OpenSMI-Core

`opensmi.server.nodesets` was removed. The NodeSet2 XMLs and the generated NodeId classes now live in
`opensmi.core.nodesets`:

```python
# Previous version (v0.2)
from opensmi.server.nodesets import RSLNodeIds

# New version (v0.3)
from opensmi.core.nodesets import RSLNodeIds
```

### Paths and names

- Paths now include the OpenSMI folders, e.g. `/M1/Components/Gripper`, `/M1/SkillSet/Pick` and
  `/M1/MethodSet/Pick`. Feasibility and precondition checks lie below their skill, e.g.
  `/M1/SkillSet/Pick/FeasibilityCheck`, while their parent is still the machinery item. Use `server.resolve(path)` to
  look up an object by path.
- `UaObject.path` is an `ObjectPath` from `opensmi.core.paths` (was `PurePosixPath`). It is never equal to a `str`,
  so compare with `ObjectPath.parse("/M1/SkillSet/Pick")` or `str(obj.path)`.
- Every object is checked when it is registered. **These now raise `ValueError`:** an empty name, a name containing
  `/`, and a path (or alias) that is already taken, e.g. two siblings with the same name.
- New naming rule for browse names: `^[A-Z][A-Za-z0-9]*$`, and not `True`, `False` or `None`. Names that break it only
  log a warning for now, but **will be rejected from opensmi-server 1.0 on**. Usernames are exempt. Rename e.g.
  `pick_place` to `PickPlace`.
- The parent of an object can't change after it was registered.
- A variable that is added to a further variable container (e.g. a component variable added to the machine's
  `Monitoring`) is referenced from there with `Organizes` instead of `HasComponent` and gets an alias path. The
  `HasComponent` reference stays with its owner.

### Users are server-wide

- Users moved from `<Machine>/MachineryBuildingBlocks/Users` to the server-wide folder `Objects/Users`, created at
  startup before any machine. Their paths are `/Users/<name>`. Machines no longer have a users folder.
- `server.users` is the users folder. `server.access_control.users` is typed `Mapping[str, User]`.
- `AccessControl.init_ua_users()` was removed.
- `Users` is a reserved machine name, `server.add_machine()` raises `ValueError` for it.
- Users belong to no machine, so no lock protects them: OPC UA writes to user nodes are always denied.

### Notifications: error code no longer in the message

Error events no longer append `|||<code>` to `Message`. The code is only in the `ErrorCode` event field. Clients that
parsed the message must read `ErrorCode` instead.

### Notifications: `SourceNode` is the object the message is about

Notification events now set `SourceNode` and `SourceName` to the object that logged the message (the `source`
argument of `ua_log_*()`, else the machinery item the `Notification` belongs to). Before, both were the `Notification`
object itself. The events are still emitted by the `Notification` object, so clients keep subscribing to it.

Clients that matched incoming events to their subscription by `SourceNode` no longer find a match and drop the events.
Match by the monitored item instead, e.g. by `event.server_handle` in asyncua. OpenSMI-Core v0.7.0 and older match by
`SourceNode`, so clients based on them (OpenSMI-Client up to now) need OpenSMI-Core v0.7.1 or newer.

Notification events are now also kept in the server's event history (up to 1000 per `Notification` object) and can be
read with `HistoryRead`.

### Other API changes

- `server.get_enum()` was renamed to `server.ensure_enum()`. Its `option_set` parameter was removed.
- `UaVariable.write_check()` is now `async`. `write_check_callback` may be a sync or async function and may return
  `False` instead of raising `OutOfRangeError` to reject a value.
- `UaFiniteStateMachine` is generic over its state type. Its unused internals were removed and `init()` no longer takes
  `state_machine_type`.

### Relative Spatial Location (RSL)

- `Position` and `Orientation` moved to `opensmi.core.rsl`, which also provides the new `Pose` (position and
  orientation together).
- Frames take a `Pose` and keyword-only arguments. `CartesianFrame(name=..., position=..., orientation=..., base=...)`
  is now `CartesianFrame(name=..., initial_pose=Pose(...), initial_base=...)`. `initial_base` may also be a raw
  `ua.NodeId`. `PositionFrame` and `WorldFrame` likewise take `initial_pose` (and `initial_base`).
- New `write_pose()`, `read_pose()` and `update_pose()` methods. `write_position()`/`write_orientation()` and their
  `read_*()` counterparts still exist.
- `SpatialObject.add_attach_point()` now takes an `initial_pose` (plus optional `name` and `initial_base`), creates
  the frame and returns it, instead of taking an initialized `CartesianFrame`. New: `add_internal_frame()` and
  `add_alternative_frame()`.
- `SpatialObject` and `SpatialObjectList` are no longer in `Monitoring`, but directly below their parent. The
  `SpatialObject` browse name is always `SpatialObject`, its `name` is written to the `Identifier` property.
- `SpatialObjectListMixin` no longer creates the `SpatialObjectList` automatically. Call
  `await self._init_spatial_object_list()` in `_init()` of your machine. It optionally takes the `WorldFrame` pose and
  a `name` (default `MachineRoomList`).

See [examples/rsl/main.py](../examples/rsl/main.py) for the current usage.

### `ParentMixin`: forward references are now resolved and checked

`ParentMixin[...]` now resolves and checks every type argument at runtime, see
[parent_mixin.py](../src/opensmi/server/mixins/parent_mixin.py). Before, it only checked plain classes and skipped
the check for everything else.

- **Forward references are checked.** A string type argument such as `ParentMixin["BaseMachineryItem"]` used to log
  `Forward reference not supported!` and skip the runtime parent check. It is now resolved in the module of the class
  and checked. **Assigning a parent of the wrong type now raises `TypeError` where it used to pass silently.** Run
  your tests to find wrongly typed parents.
- **More type forms are supported:** unions (`ParentMixin[BaseMachine | MyComponent]`), parameterized generics (only
  the origin is checked, e.g. `UaObjectContainer` for `UaObjectContainer[Resource]`), `Annotated[...]` (the
  metadata is ignored) and TypeVars (their default, else their bound, else `UaObject`).
- **Unsupported forms fail early.** `Literal[...]`, a `None` member (e.g. `Optional[X]` or `X | None`) and other
  forms that are not a class now raise `TypeError` when the class is defined. Before, they logged a warning and skipped
  the check. The parent of an `UaObject` is never `None`, so remove `None` from the type argument.
- **`TYPE_CHECKING`-only names are matched by class name.** If a forward reference cannot be resolved, e.g. because
  the class is only imported under `if TYPE_CHECKING:`, the parent passes if any class in its MRO has that name.
- **A class without a `ParentMixin[...]` argument silently uses `UaObject`.** Subclassing a bare `ParentMixin` used to
  log `... does not specialize ParentMixin`. It now accepts any `UaObject` parent without logging a warning.

## OpenSMI v0.1 -> v0.2

- `Server.ua_import_xml()` parameters are keyword-only: use `ua_import_xml(path=...)`. It also accepts
  `xml_string=...` instead of a path.
- The deprecated `add_*_variable()` methods were re-added for backwards compatibility. Use `UaVariable` class
  attributes in the variable containers (`ParameterSet`, `Monitoring`, etc.) instead.
