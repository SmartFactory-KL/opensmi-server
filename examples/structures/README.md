# Structures

Mints two structure DataTypes without a nodeset, `Point` (x, y, z as Double) and `Waypoint` (a name and a nested
`Point`), and publishes them as machine parameters: `Target` holds one `Waypoint`, `Path` holds an array of `Point`
guarded by `structure_array_write_check`. The server changes `Target` once after it starts.

`client.py` is plain asyncua. It calls `load_data_type_definitions`, which builds the classes from the
`DataTypeDefinition` the server published, reads both variables and tries to write a `Waypoint` into `Path`.

Run the server in one terminal and the client in another, from the repository root:

```
python examples/structures/server.py
python examples/structures/client.py
```

The server logs its startup and ends with a line like `Serving 'urn:example:structures' on opc.tcp://0.0.0.0:4841/server`.
The client prints:

```
Target: 'bay 3' at (1.5, -2.0, 0.25)
Path: 1 point(s), first at x=0.0
Writing a Waypoint to Path was refused: BadTypeMismatch
```
