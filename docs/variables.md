# Variables

Machinery items, skills and methods hold their values in `UaVariable`s inside the variable containers `ParameterSet`,
`Monitoring` and `FinalResultData` (see [Machinery Items](machinery_item.md)). This page covers values without a valid
value, and values that belong together: structures, and groups of variables.

## Bad status

When a variable has no valid value, e.g. because the connection to its data source is lost, mark it with a bad
status instead of leaving the last value. Clients then see the status and no value:

```python
from asyncua import ua

await self.monitoring.Temperature.write_status(ua.StatusCodes.BadNoCommunication)
...
await self.monitoring.Temperature.write(21.5)  # valid again: the status is Good
```

`write_status()` accepts bad statuses only; a good or uncertain status comes with a value, so use `write()`. A bad
status is not checked: every variable accepts it, also one that is not `optional_ok`, and its `write_check_callback`
is not called.

## Structures

A variable whose `initial_value` is a dataclass instance holds a structure. The server creates the OPC UA DataType
from the dataclass when the variable is created, and the variable reads and writes instances of it:

```python
from dataclasses import dataclass, field

from opensmi.server import ParameterSet, UaVariable


@dataclass
class Dimensions:
    length: float = 0.0
    width: float = 0.0


@dataclass
class Workpiece:
    id: str = ""
    dimensions: Dimensions = field(default_factory=Dimensions)


class Parameters(ParameterSet):
    CurrentWorkpiece = UaVariable(initial_value=Workpiece("W-001", Dimensions(120.0, 80.0)))
    """The workpiece in the machine."""
```

Fields are `float`, `int`, `str`, `bool`, `bytes`, another dataclass, or a `list` of one of these. A list of dataclass
instances is a variable too; if it starts empty, name the class with `variable_type`. Every write is checked: the
variable accepts only an instance of its class (or a list of them) and refuses anything else with `BadTypeMismatch`.

A client needs no class of its own. asyncua's `Client.load_data_type_definitions()` builds one from the
`DataTypeDefinition` the server publishes. The module docstring of `opensmi.server.structures` has the details, e.g.
how to pick other OPC UA number types for a field.

## Complex variables

A variable that holds one structure (not a list) is a complex variable: below the variable is one child variable per
field, and below a nested structure its fields again. The browse name of a child is its field name in PascalCase:

```
ParameterSet
  CurrentWorkpiece            Workpiece    (the whole structure)
    Id                        String
    Dimensions                Dimensions   (complex again)
      Length                  Double
      Width                   Double
```

A list, also of dataclass instances, is one child of its own, without children for its elements.

Clients can read, subscribe to and write each child like any other variable, so a client without the structure's
class can still show and change `CurrentWorkpiece/Dimensions/Length`. In Python, `variable.children` maps the names to
the child variables, and `variable["Dimensions"]["Length"]` is one of them:

```python
length = self.parameter_set.CurrentWorkpiece["Dimensions"]["Length"]
await length.write(95.0)
```

To change one field from server code, write the child rather than reading, changing and writing the whole structure.
A write to a child is merged into the current structure while other writes to the variable wait, so a concurrent
write to another field, e.g. by a client, is kept. Between your `read()` and `write()` of the whole structure,
another write can land, and yours would undo it.

The variable and its children always agree:

- A write to the variable also writes all its children, and a write to a child also writes the variable and the
  structures on the way up. All of them get one `SourceTimestamp` and the status of the write.
- A bad status (e.g. `BadNoCommunication`, see [Bad status](#bad-status)) has no value and is not checked. Written to
  the variable or to any child, it reaches the variable and all its children, and none of them has a value.
- Every write is checked once, by the variable that holds the whole structure, on the whole new structure. Its
  `write_check_callback` therefore also sees writes to a child and can check rules across fields. A refused write
  changes nothing. A `write_check_callback` only checks: it must not write any variable.
- The children share the access of the variable: `writable`, `bypass_lock` and `minimum_access_level`, also after
  `set_writable()`. They have no `write_check_callback` of their own.
- While the variable has a bad status, a write to a child is refused with that status, since there is no structure to
  change. Write the whole structure first.

## Groups

A `VariableGroup` is an Object in a variable container that holds variables and further groups, for variables that
belong together but are not one structure. Declare it like a variable:

```python
from opensmi.server import ParameterSet, UaVariable, VariableGroup


class FollowPath(VariableGroup):
    MaxVelX = UaVariable(initial_value=0.5)
    """Maximum forward velocity in m/s."""


class Controller(VariableGroup):
    UseSimTime = UaVariable(initial_value=False)
    """Whether the controller uses the simulation time."""

    FollowPath = FollowPath()


class Parameters(ParameterSet):
    Controller = Controller()
```

Clients browse it as `ParameterSet/Controller/FollowPath/MaxVelX`. A group takes the writability and the
`minimum_access_level` of its container, so the variables declared in it are writable in a `ParameterSet` and
read-only in `Monitoring`, as if they were declared in the container itself.

Add and remove groups while the server runs with `add_group()` and `remove_group()`. Removing a group removes
everything below it:

```python
sensors = await self.monitoring.add_group("Sensors")
await sensors.add(UaVariable(initial_value=0.0, name="Level"))
...
await self.monitoring.remove_group("Sensors")
```

In Python, a declared group is an attribute of its container (`parameters.Controller.FollowPath.MaxVelX`), and
`container.groups` maps the names of all groups to them, declared or added. The rest of the container interface covers
its variables only: `container["MaxVelX"]`, `"MaxVelX" in container`, `len(container)` and `for variable in
container` do not see groups. `read_all()`, `write_all()` and `reset_all()` include the groups, as nested mappings:

```python
await parameters.read_all()
# {"Controller": {"UseSimTime": False, "FollowPath": {"MaxVelX": 0.5}}}
```

`reset_all()` skips variables created with `resettable=False`, e.g. counters since server start; their own `reset()`
still resets them.

## Structure or group?

A complex variable is one value: it is valid as a whole or not at all. A bad status on any of its nodes reaches all
of them, and a client can read it atomically. A group is only a namespace: each variable in it has its own value and
status, and the group itself has none.

- Use a **structure** for values that arrive and are valid together, from one source, e.g. a workpiece record or a
  pose read from the controller in one go.
- Use a **group** for values that come from independent sources and can fail separately, e.g. the readings of two
  sensors. Then one lost sensor marks only its own variables bad, and the others stay readable.
