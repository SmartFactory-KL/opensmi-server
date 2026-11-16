# Structures

Shows structured values defined as dataclasses. The machine has two parameters: `CurrentWorkpiece`, a `Workpiece`
(id, material and nested `Dimensions`), and `Measurements`, a list of `Measurement` (name, value, in tolerance). They
are declared like any other variable, with a dataclass instance as `initial_value`. The structure types are created on
the server when the variables are created, and the server reads and writes the dataclass objects directly. Every
write is checked: `CurrentWorkpiece` accepts only a `Workpiece` and `Measurements` only a list of `Measurement` (a
list that starts empty names its class with `variable_type`). The server changes the workpiece once after it starts.

A dataclass value is a complex variable: the variable holds the whole structure, and below it is one child variable per
field (`CurrentWorkpiece/Dimensions/Length`), recursively. The browse name of a child is the field name in PascalCase,
its display name is the field name. A client can read, write or subscribe to a single field, and the structure always
agrees with its fields. A write is checked once, at the parent, so a `write_check_callback` always sees the whole
structure. In Python, `variable["Dimensions"]["Length"]` is the child variable.

The parameters also hold a declared group, `Gripper` (a `VariableGroup`, an Object that holds variables), and the
server adds a group named `station 1: left` to the monitoring at runtime. A name that comes from runtime data is added
with `runtime_keyed=True`, which only requires a valid path segment (not empty, no `/`) instead of the naming rule.

`client.py` is plain asyncua and knows none of the classes. It calls `load_data_type_definitions`, which builds them
from the `DataTypeDefinition` the server published, reads the workpiece, reads and writes its length through the child
variable, reads the workpiece again, browses the two groups, adds a measurement and tries to write a `Workpiece` into
`Measurements`.

Run the server in one terminal and the client in another, from the repository root:

```
python examples/structures/server.py
python examples/structures/client.py
```

The server logs its startup and ends with the line `Serving on opc.tcp://0.0.0.0:4841/server`.
The client prints:

```
Workpiece: W-002 (aluminium), 90.0 x 60.0 x 5.0 mm
Length: 90.0 mm
Workpiece now: 95.0 mm long
Gripper: ['Force']
Monitoring group 'station 1: left': ['Temperature']
Measurements: ['length']
Measurements now: ['length', 'width']
Writing a workpiece to Measurements was refused: BadTypeMismatch
```
