# Machinery Items

A MachineryItem is the common abstraction behind both [Machines](#machines) and [Components](#components) — every node
in the machine hierarchy is one.

It provides:

- **`Attributes`** *(optional)* — static, read-only descriptive info, e.g. an axis's travel range or a robot's
  reach.
- **`Identification`** *(mandatory)* — static, read-only identity info: ID, serial number, manufacturer, model, etc.
- **`ParameterSet`** *(optional)* — dynamic, writable parameters, e.g. a speed override or a gripper's target force.
- **`Monitoring`** *(optional)* — dynamic, read-only runtime info, e.g. an axis's current position or a gripper's
  live current draw.
- **`Components`** *(optional)* — nested subcomponents, e.g. a robot composed of individual axes and a gripper.
- **`SkillSet`** *(optional)* — the (asynchronous) [Skills](skills.md) this item exposes.
- **`MethodSet`** *(optional)* — the (synchronous) [Methods](methods.md) this item exposes.
- **`Lock`** — manages exclusive (external) access to the MachineryItem and everything beneath it in the
  hierarchy. Parameters are writable, and Skill/Method OPC-UA calls are callable, only while the lock is held (on
  top of the caller's own access level). Optional for Components, mandatory for Machines.

## Components

A **Component** is a MachineryItem representing one piece of the hierarchy below a Machine, and may itself
contain further subcomponents. For example, a **robot** is a component composed of several **axis** components
plus a **gripper** component — each axis and the gripper are themselves Components, each with their own Skills (e.g. an
axis's "MoveTo", a gripper's "Open"/"Close"), parameters, and monitoring, nested under the robot. A
component's `Lock` is optional: it *may* enforce exclusive access over itself and its sub-hierarchy, but
fine-grained components like a single axis often don't need one.

## Machines

A **Machine** is a Component with one added requirement: it sits at the top of its own hierarchy — the unit a
client addresses as "the machine" — and its `Lock` is mandatory. For example, a machine might combine a **robot**
(itself composed of axes and a gripper, as above), a **storage** component holding parts or fixtures, and a **port**
component acting as the handoff point to a transport system or AMR.

The machine's lock guards exclusive access across all of it — so that, say, an AMR docking at the port and an operator
manually jogging the robot can't contend for the same resources at once. Since a machine is the entry point coordinating
potentially conflicting use across everything beneath it, exclusive access can't be optional the way it can for an
individual component further down (an axis or the gripper alone rarely needs its own lock).

> [!note]
> "Top of the hierarchy" is per-machine, not per-server: a single OPC-UA server may expose **multiple
machines** side by side — e.g. aggregating several independent robot cells into one server — each with its own
Machine root and its own `Lock`, rather than one server implying exactly one machine.

## StartupSkill

A MachineryItem may designate one continuous Skill as its **StartupSkill**. Beyond bringing the
item's other skills to `Ready`, it also owns the item's overall state machine and supervises it at runtime —
watching skill states and `Monitoring` variables, and reacting when something goes wrong. For example, a robot's
**StartupSkill** might halt itself if an axis unexpectedly halts, or if the gripper's current draw drifts outside an
allowed range.

> [!warning]
> When the **StartupSkill** halts, it halts all Skills it manages along with it — a single point of failure by design,
so a fault anywhere under its supervision brings the whole item to a safe state together.

---

*This documentation was drafted with AI assistance (Claude, Anthropic) and reviewed/edited by the author before
publication.*
