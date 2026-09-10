# Development (Work in progress)

> [!important] 
> You do **not** need to read this if you just want to use this as a normal Python library!

[[_TOC_]]

## Design Goals

- API as simple to use as possible, ideally little to no asyncua contact for end users
    - Self-explaining where possible
  - As many type hints as possible
- API of server and client should be as similar as possible
- API should be generic enough to not require changes when naming of underlying Skill node set changes, i.e. Module (v1-v3) →
  Machine (v4)
- Expose raw OPC UA as little as possible, abstract the communication away
  - Later: Communication via OPC UA, shared memory, etc. (⇒ "OT-Bus")

### Non-Goals

- Highly specialized functionality only useful for a single module/use case

## Design Decisions

- No mutable globals
- No async properties - too confusing, no support for setters
- Multiple clients must be supported, that is required for some use cases
- OPC UA related variables/methods/etc. shall be prefixed by `ua_`. Most if not all such variables should be kept
  private, so the prefix is `_ua_`. (Goal: abstract communication)
- Force keyword-only arguments for non-obvious situations:
  ```python 
  async def wait_for_state(self, state: SkillState, *, timeout: float | None = 60) -> None: ...
  ```
  `wait_for_state()` mentions `state`, so the first argument is expected to be the state. `timeout` however is a
  (optional) configuration option. Forcing keyword-only makes the user-code more readable and future additions to the
  API trivial.

## Contribution

1. Open an Issue discussing the planned changes/additions with the maintainer(s).
2. Fork this repo
3. Implement your changes/additions including Type Hints and Tests.
4. Make sure ALL tests succeed. Do not modify existing tests unless you have a very good reason!
5. Start a new Merge Request to integrate your contribution.

### Setup

Clone your forked repo into any folder (outside any other project!) and then run this command within it:

```shell
uv sync --all-extras
```

This will also install all requirements.

## Tools

Currently, we are using the following tools:

| Tool         | Notes etc.                                      |
|--------------|-------------------------------------------------|
| PyCharm      | Recommended Python IDE (although you do you...) |
| basedpyright | Static Type Checking                            |
| pytest       | Unit tests                                      |
| ruff         | Linting & Formatting                            |
