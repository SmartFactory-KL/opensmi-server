# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: CC0-1.0

"""Example to demonstrate the use of OPC UA dictionary entries to provide semantic information.

Dictionary entries can be added either via constructors or method ``add_dictionary_entry``.
"""

import asyncio

from asyncua import ua

from opensmi.server import BaseMachine, ParameterSet, Server, UaVariable
from opensmi.server.mixins import ParameterSetMixin
from opensmi.server.ua_dictionary_entry import IrdiDictionaryEntry, UriDictionaryEntry


class _ParameterSet(ParameterSet):
    FaxNumber = UaVariable(
        initial_value="+49.631.12345-6789",
        dictionary_entry=IrdiDictionaryEntry(
            semantic_id="0112/2///61360_7#CBA202#001",  # IRDI from IEC Common Data Dictionary (https://cdd.iec.ch/)
            name="fax number",  # should be IEC CDD preferred name
        ),
    )
    """Most technologically advanced contact information, straight from the cutting edge of German bureaucracy."""


class ExampleMachine(ParameterSetMixin[_ParameterSet], BaseMachine):
    """Semantically well-annotated machine whose main purpose is to receive faxes."""

    async def _init(self) -> None:
        await super()._init()
        await self.add_dictionary_entry(
            UriDictionaryEntry("https://smartfactory.de/dictionary/machine/example-machine")
        )

    async def _write_identification(self) -> None:
        # These identification variables must be set for machines
        await self.identification.SerialNumber.write("1234-56789-abc")
        await self.identification.ProductInstanceUri.write("urn:smartfactory.de-model:snr-1234-56789-abc")
        await self.identification.Manufacturer.write(
            ua.LocalizedText("Technologie-Initiative SmartFactory KL e. V.", "de-DE")
        )


async def main() -> None:
    async with Server() as server:  # will properly shut down the server
        await server.add_machine(ExampleMachine())  # add machine(s)
        await server.start(blocking=True)  # start the server and block while it is running


if __name__ == "__main__":
    asyncio.run(main())
