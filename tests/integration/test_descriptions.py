# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from collections.abc import AsyncGenerator, Sequence

import pytest
import pytest_asyncio
from asyncua import ua
from structlog.testing import capture_logs
from structlog.typing import EventDict
from typing_extensions import override

from opensmi.server import BaseMachine, Server, UaVariable
from opensmi.server.mixins import ParameterSetMixin
from opensmi.server.ua_variable_containers import (
    MachineIdentification,
    MachineryComponentIdentification,
    ParameterSet,
)

_MACHINERY_MANUFACTURER = "A human-readable, localized name of the manufacturer of the MachineryItem."


class _Parameters(ParameterSet):
    WithDocstring = UaVariable(0)
    """From docstring."""
    WithParameter = UaVariable(0, description="From parameter.")
    WithBoth = UaVariable(0, description="From parameter.")
    """From docstring."""
    WithNone = UaVariable(0)


class _DescribedMachine(ParameterSetMixin[_Parameters], BaseMachine):
    """Machine for testing descriptions."""

    @override
    async def _write_identification(self) -> None:
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(ua.LocalizedText("SmartFactory", "de-DE"))


@pytest_asyncio.fixture
async def started(server: Server) -> AsyncGenerator[tuple[_DescribedMachine, list[EventDict]]]:
    machine = _DescribedMachine()
    with capture_logs() as logs:
        async with server:
            await server.add_machine(machine)
            await server.start(blocking=False)
            yield machine, logs


def _missing(logs: Sequence[EventDict]) -> set[str]:
    return {log["path"] for log in logs if log["event"] == "Missing description"}


async def _description(variable: UaVariable) -> str:
    return (await variable.ua_node.read_description()).Text or ""


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("WithDocstring", "From docstring."),
        ("WithParameter", "From parameter."),
        ("WithBoth", "From docstring."),  # docstring takes precedence
    ],
)
@pytest.mark.asyncio
async def test_description_of_new_variable(started, name: str, expected: str) -> None:
    machine, logs = started
    assert await _description(machine.parameter_set[name]) == expected
    assert f"/{machine.name}/ParameterSet/{name}" not in _missing(logs)


@pytest.mark.asyncio
async def test_missing_description_warns_despite_asyncua_placeholder(started) -> None:
    machine, logs = started
    # asyncua uses the browse name as placeholder, which does not count as description
    assert await _description(machine.parameter_set.WithNone) == "WithNone"
    assert _missing(logs) == {f"/{machine.name}/ParameterSet/WithNone"}


@pytest.mark.asyncio
async def test_annotation_docstring_of_existing_variable(started) -> None:
    machine, _ = started
    # inherited annotation docstring of `MachineryComponentIdentification`, overwriting the node set description
    assert await _description(machine.identification.Manufacturer) == "Name of the manufacturer."


@pytest.mark.asyncio
async def test_existing_description_from_node_set_is_kept(server: Server, monkeypatch: pytest.MonkeyPatch) -> None:
    for cls in (MachineryComponentIdentification, MachineIdentification):
        monkeypatch.setattr(cls, "__attr_docs__", {})  # pretend there are no docstrings

    machine = _DescribedMachine()
    with capture_logs() as logs:
        async with server:
            await server.add_machine(machine)
            await server.start(blocking=False)
            assert await _description(machine.identification.Manufacturer) == _MACHINERY_MANUFACTURER
    assert f"/{machine.name}/Identification/Manufacturer" not in _missing(logs)
