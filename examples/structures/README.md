# Structures

Shows structured values defined as dataclasses. The machine has two parameters: `CurrentWorkpiece`, a `Workpiece`
(id, material and nested `Dimensions`), and `Measurements`, a list of `Measurement` (name, value, in tolerance). They
are declared like any other variable, with a dataclass instance as `initial_value`. The structure types are created on
the server when the variables are created, and the server reads and writes the dataclass objects directly. A list of
a dataclass is checked on every write: only a list of that dataclass is accepted. The server changes the workpiece once
after it starts.

`client.py` is plain asyncua and knows none of the classes. It calls `load_data_type_definitions`, which builds them
from the `DataTypeDefinition` the server published, reads both variables, adds a measurement and tries to write a
`Workpiece` into `Measurements`.

Run the server in one terminal and the client in another, from the repository root:

```
python examples/structures/server.py
python examples/structures/client.py
```

The server logs its startup and ends with the line `Serving on opc.tcp://0.0.0.0:4841/server`.
The client prints:

```
Workpiece: W-002 (aluminium), 90.0 x 60.0 x 5.0 mm
Measurements: ['length']
Measurements now: ['length', 'width']
Writing a workpiece to Measurements was refused: BadTypeMismatch
```
