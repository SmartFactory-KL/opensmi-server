# OpenSMI Server

**Open Smart Machine Interface (SMI)** provides an abstract, standardized, vendor-neutral way to interact safely
with real (or simulated) machines. **OpenSMI** is an asynchronous Python framework, built on top of
[asyncua](https://github.com/FreeOpcUa/opcua-asyncio) for building SMI-compliant OPC UA servers.

It gives you the building blocks for exposing manufacturing capabilities — axes, grippers, robots, conveyors, whole
machines — over a standardized, hierarchical OPC UA information model, without having to hand-roll everything yourself.

## Why OpenSMI

A common real-world use case is as an **adapter in front of a Programmable Logic Controller (PLC)**: many PLCs can speak
OPC UA but don't natively expose SMI's richer skill model — suspendable/resumable skills, composite orchestration,
feasibility/precondition checks, standardized locking. OpenSMI lets you sit in front of such a PLC and:

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
pip install opensmi-server
```

## Quick Start

Define a skill, add it to a machine, and start the OPC UA server:

```python
import asyncio

from asyncua import ua
from opensmi.core import Unit

from opensmi.server import BaseMachine, BaseSkillFinalResultData, BaseSkillFinite, ParameterSet, Server, UaVariable
from opensmi.server.mixins import FinalResultDataMixin, ParameterSetMixin, ParentMixin


class ExampleSkillParameterSet(ParameterSet):
    """Parameters of the example skill."""

    x = UaVariable(0, unit=Unit.NANOAMPERE, range=(0, 10))
    y = UaVariable(0, unit="nA", range=(0, 10))


class ExampleSkillSimpleFinalResultData(BaseSkillFinalResultData):
    """Final result data of the example skill."""

    ComputationResult = UaVariable(0, unit="nA", range=(0, 20))


class ExampleSkill(
    ParentMixin["ExampleMachine"],  # provides type-checkable parent type
    ParameterSetMixin[ExampleSkillParameterSet],  # provides type-checkable parameters
    FinalResultDataMixin[ExampleSkillSimpleFinalResultData],  # provides type-checkable results
    BaseSkillFinite,  # provides finite skill logic etc.
):
    async def _handle_running(self) -> None:
        # define logic that is executed in the RUNNING state

        # read our parameters
        x = await self.parameter_set.x.read()
        y = await self.parameter_set.y.read()

        # simulate long-running calculation etc.
        await asyncio.sleep(1)

        # we are done, write return variables
        await self.final_result_data.ComputationResult.write(x + y)


class ExampleMachine(BaseMachine):
    async def _init(self) -> None:
        await super()._init()

        await self.add_skill(ExampleSkill())

    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write(
            "urn:smartfactory.de-model:snr-1234-56789-abc"
        )
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory-KL e. V.", "de-DE")
        )


async def main() -> None:
    async with Server() as server:  # will properly shut down the server
        await server.add_machine(ExampleMachine())  # add machine(s)
        await server.start(blocking=True)  # start the server and block while it is running


if __name__ == "__main__":
    asyncio.run(main())
```

### Usage

1. Connect to the server with any OPC UA client
   (e.g. [UaExpert](https://www.unified-automation.com/products/development-tools/uaexpert.html))
   at `opc.tcp://localhost:4841`, authenticating as user `operator` with password `operator`.
2. Navigate to `Objects/Machines/ExampleMachine`.
3. Under `ExampleMachine/Lock`, call the `InitLock()` method to acquire exclusive access — required before you can
   write parameters or call skill methods.
4. Navigate to `ExampleMachine/SkillSet/ExampleSkill/SkillExecution`. This is where the skill's `ParameterSet`,
   `StateMachine`, and `FinalResultData` etc. live.
5. Under `ParameterSet`, write values for `x` and `y`.
6. Under `StateMachine`, call `Reset()`. Skills start in the `Halted` state and must be reset to `Ready` before
   they can run.
7. Call `Start()`. The skill moves through `Starting` → `Running` → `Completing` → `Completed`.
8. Once `StateMachine`'s `CurrentState` reads `Completed`, read the result from `ComputationResult` under
   `FinalResultData`.

**Explore the safety features:**

- Try writing parameters or calling skill methods *without* holding the Lock — it will be rejected.
- Try writing `x` or `y` outside their allowed range — it will be rejected.
- Try acquiring the Lock while authenticated as user `visitor` with password `visitor` — it will be rejected.

See [here](examples) for more OpenSMI server examples.

## Core Concepts

| Concept                                             | What it is                                                                                                                                    |
|-----------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------|
| [**Machinery Items**](docs/machinery_item.md)       | Common base for `Machines` and `Components`: attributes, identification, parameters, monitoring, sub-components, skills, methods, etc.        |
| [**Components**](docs/machinery_item.md#components) | One piece of a machine's hierarchy, e.g. an axis or a gripper. May nest further sub-components.                                               |
| [**Machines**](docs/machinery_item.md#machines)     | The top-level unit a client addresses as "the machine," e.g. a robot + storage + transport port. A single server can expose several machines. |
| [**Skills**](docs/skills.md)                        | Asynchronous, stateful capabilities  — finite (run once, complete) or continuous (run indefinitely), atomic or composite.                     |
| [**Methods**](docs/methods.md)                      | Synchronous, quick operations — no state machine, just `call()` in, result out.                                                               |

## Project Status

OpenSMI began as a closed-source project at [SmartFactory-KL](https://smartfactory.de/), in active use in our model
factory since 2020. Its OPC UA information model has been refined across many iterations of research and
demonstrator use. The framework was open-sourced in 2026 following substantial refactoring and cleanup.

Most of the framework is stable and well-tested, but it remains pre-1.0 — expect minor API changes before the 1.0
release.

> [!warning]
> Currently, OpenSMI is a research and prototyping framework. Python is well suited for rapid development and
experimentation, but is generally **not recommended for industrial deployment**. Given sufficient interest and
(financial) support, we'd like to port OpenSMI's concepts to languages better suited for industrial use —
e.g. C# on the [official OPC Foundation .NET stack](https://github.com/OPCFoundation/UA-.NETStandard), or C++ using
> [open62541](https://open62541.org/). Get in touch if that's something you'd want to support.

<!-- 
## OPC UA Nodesets
-->

## Publications

Get more information of OpenSMI by reading our publications:

### Scientific Publications

| Title                                                                                                                                                                                                 | Content                                                                            |
|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|------------------------------------------------------------------------------------|
| [**Seamless Machine Integration in Smart Manufacturing: Utilizing OPC UA for Machinery with Skill-Based Engineering of Varying Granularity**](https://ieeexplore.ieee.org/abstract/document/11599075) | Application of skills in robotics and an introduction to OpenSMI's OPC UA modeling |
| [**Developing a skill-based flexible transport system using OPC UA**](https://www.degruyterbrill.com/de/document/doi/10.1515/auto-2022-0115)                                                          | Application of skills in intralogistic                                             |
| [**Interaction between FeasibilityCheck, PreconditionCheck and SkillExecution in skill-based machining**](https://ieeexplore.ieee.org/abstract/document/10275520)                                     | Application of skills in machining                                                 |

### Joint Publications

| Title                                                                                                                                                                                  | Content                                                                                                               |
|----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------------------------------|
| [**Capabilities and Skills in Production Automation**](https://www.vdma.eu/documents/34570/77803117/Capabilities_and_Skills_in_Production_Automation_EN.pdf)                           | Guidline for capabilities and skills with a focus on OPC UA                                                           |
| [**Information Model for Capabilities, Skills & Services**](https://www.plattform-i40.de/IP/Redaktion/DE/Downloads/Publikation/CapabilitiesSkillsServices.pdf)                         | Presenting an information model for flexible manufacturing in Industry 4.0 based on capabilities, skills and services |
| [**Capabilities, Skills and Services CSS Model Extensions and Engineering Methodology**](https://www.plattform-i40.de/IP/Redaktion/DE/Downloads/Publikation/2025-i40-capabilities.pdf) | Refinement of the information model for capabilities, skills and services                                             |

<!-- 
## Contributing
-->

## License

[MIT](LICENSES/MIT.txt)

---

*This text was drafted with AI assistance (Claude, Anthropic) and reviewed/edited by the author before
publication.*
