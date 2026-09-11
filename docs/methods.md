# Methods

A Method is a self-contained OPC-UA operation exposed by a machinery item, structurally similar to
a [skill](skills.md). It is its own object with a `ParameterSet`, `Monitoring`, and `FinalResultData` — but without a
skill's state machine.

The core distinction is synchronicity:

- A **Method is essentially a synchronous function call**: the caller writes
  parameters, invokes `call()`, and blocks until the result comes back, all within the OPC-UA request timeout.
- A **Skill is an asynchronous operation**: `start()` returns immediately once the transition is accepted, the actual
  work happens in the background over however long it takes rather than by blocking on the call itself.

## Basics

Each method is exposed over OPC-UA with a single `call()` method and, like a skill, optionally provides:

- **`ParameterSet`** — the inputs the method needs, written by the caller before invoking `call()`.
- **`FinalResultData`** — the output produced by the call, available as soon as `call()` returns.
- **`Monitoring`** — insight surfaced during the call, if applicable. Mainly exists for consistency.

Because a method call blocks the caller for its full duration, methods are expected to execute quickly — well
within the OPC-UA request timeout — rather than the potentially long-running or indefinite work a skill is built
for.

## When to Use a Method vs. a Skill

Use a **method** when the operation is naturally synchronous: fast, deterministic, and the caller genuinely wants
to wait for the result before doing anything else — no need to suspend, resume, or monitor progress mid-flight.

Use a **skill** when the operation is naturally asynchronous: it may take a long time or run indefinitely, benefits
from being suspendable/resumable, and callers need to observe it progressing through states rather than blocking on a
single call.

---

*This documentation was drafted with AI assistance (Claude, Anthropic) and reviewed/edited by the author before
publication.*
