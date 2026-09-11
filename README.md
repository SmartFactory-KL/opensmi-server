# OpenSMI Server

**SMI** stands for **Smart Machine Interface** — an abstract, standardized, vendor-neutral way to interact safely
with real (or simulated) machines. **OpenSMI** is an asynchronous Python framework built on top of asyncua for building
SMI-compliant OPC-UA servers

It gives you the building blocks for exposing manufacturing capabilities — axes, grippers, robots, conveyors, whole
machines — over a standardized, hierarchical OPC-UA information model, without having to hand-roll everything.

## Why OpenSMI

A common real-world use case is as an **adapter in front of a PLC**: many PLCs can speak OPC-UA but don't natively
expose SMI's richer skill model — suspendable/resumable skills, composite orchestration, feasibility/precondition
checks, standardized locking. OpenSMI lets you sit in front of such a PLC and:

- **Adapt** what the PLC already exposes into proper SMI skills/methods, so any SMI-aware client can talk to it
  uniformly regardless of vendor or PLC platform.
- **Extend** it: if the PLC only implements simple atomic skills (e.g. `MoveTo`, `Open`, `Close`), compose them into
  higher-level composite skills (e.g. `PickAndPlace`) in Python — where orchestration logic is faster to write,
  test, and iterate on than in PLC ladder/structured text.

This keeps low-level, safety-critical motion on the PLC where it belongs, while higher-level sequencing and
coordination logic lives in a language better suited for rapid development.

## Requirements

- Python 3.11+

## Installation

```bash
pip install opensmi
```

## Quick Start

Define a skill, add it to a machine, and start the OPC-UA server:

```python
# TODO
```

## Core Concepts

| Concept                                             | What it is                                                                                                                                    |
|-----------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| [**Machinery Items**](docs/machinery_item.md)       | Common base for `Machines` and `Components`: attributes, identification, parameters, monitoring, sub-components, skills, methods, etc.        |
| [**Components**](docs/machinery_item.md#components) | One piece of a machine's hierarchy, e.g. an axis or a gripper. May nest further sub-components.                                               |
| [**Machines**](docs/machinery_item.md#machines)     | The top-level unit a client addresses as "the machine," e.g. a robot + storage + transport port. A single server can expose several machines. |
| [**Skills**](docs/skills.md)                        | Asynchronous, stateful capabilities  — finite (run once, complete) or continuous (run indefinitely), atomic or composite.                     |
| [**Methods**](docs/methods.md)                      | Synchronous, quick operations — no state machine, just `call()` in, result out.                                                               |

## Project Status

## Project Status

OpenSMI began as a closed-source project at [SmartFactory-KL](https://smartfactory.de/), in active use in our model
factory since 2020. Its OPC-UA information model has been refined across many iterations of research and
demonstrator use. The framework was open-sourced in 2026 following substantial refactoring and cleanup.

Most of the framework is stable and well-tested, but it remains pre-1.0 — expect minor API changes before the 1.0
release.

> [!warning]
> Currently, OpenSMI is a research and prototyping framework. Python is well suited for rapid development and
experimentation,
> but is generally **not recommended for industrial deployment**. Given sufficient interest and (financial) support,
we'd like to
> port OpenSMI's concepts to languages better suited for industrial use — e.g. C# on the
> [official OPC Foundation .NET stack](https://github.com/OPCFoundation/UA-.NETStandard), or C++ using
> [open62541](https://open62541.org/). Get in touch if that's something you'd want to support.

## Contributing

## License

MIT — see [here](LICENSES/MIT.txt) for details.

---

*This text was drafted with AI assistance (Claude, Anthropic) and reviewed/edited by the author before
publication.*
