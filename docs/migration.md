# Migration

This guide helps you upgrade to the newest version of OpenSMI-Server. Sections are ordered newest first. If you skip
versions, apply every section between your version and the newest one, oldest first.

- [OpenSMI v0.2 -> v0.3 (unreleased)](#opensmi-v02---v03-unreleased)
- [OpenSMI v0.1 -> v0.2](#opensmi-v01---v02)

## OpenSMI v0.2 -> v0.3 (unreleased)

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
