# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import pytest

from opensmi.server.ua_dictionary_entry import IrdiDictionaryEntry, UriDictionaryEntry

IRDI_IEC_CDD = "0112/2//a/61360_4#AAE867#001"
IRDI_ISO_5598 = "0112-1-a-18582#KAA802#s"
IRDI_ECLASS = "0173/1///#02-8AD792#s"

URL = "https://example.com"
URN = "urn:isbn:0451450523"
STR = "abc"


@pytest.mark.parametrize(
    "semantic_id",
    [
        pytest.param(IRDI_IEC_CDD, id="iec-cdd"),
        pytest.param(IRDI_ISO_5598, id="iso-5598"),
        pytest.param(IRDI_ECLASS, id="eclass"),
    ],
)
def test_irdi_dictionary_entry_accepts_valid_irdi(semantic_id: str) -> None:
    entry = IrdiDictionaryEntry(semantic_id=semantic_id)
    assert entry.semantic_id == semantic_id


@pytest.mark.parametrize(
    "semantic_id",
    [
        pytest.param(URL, id="url"),
        pytest.param("https://example.com/dict/entry", id="url-with-path"),
        pytest.param("https://example.com/dict#entry", id="url-with-fragment"),
        pytest.param(URN, id="urn"),
        pytest.param(STR, id="plain-string"),
        pytest.param("", id="empty"),
        pytest.param(f"{IRDI_IEC_CDD}\n", id="trailing-newline"),
    ],
)
def test_irdi_dictionary_entry_rejects_invalid_irdi(semantic_id: str) -> None:
    with pytest.raises(ValueError, match="not a valid IRDI"):
        IrdiDictionaryEntry(semantic_id=semantic_id)


@pytest.mark.parametrize(
    "semantic_id",
    [
        pytest.param(URL, id="url"),
        pytest.param(URN, id="urn"),
    ],
)
def test_uri_dictionary_entry_accepts_valid_uri(semantic_id: str) -> None:
    entry = UriDictionaryEntry(semantic_id=semantic_id)
    assert entry.semantic_id == semantic_id


@pytest.mark.parametrize(
    "semantic_id",
    [
        pytest.param(IRDI_IEC_CDD, id="iec-cdd"),
        pytest.param(IRDI_ISO_5598, id="iso-5598"),
        pytest.param(IRDI_ECLASS, id="eclass"),
        pytest.param(STR, id="plain-string"),
        pytest.param("", id="empty"),
        pytest.param("https://example.com/a b", id="inner-space"),
    ],
)
def test_uri_dictionary_entry_rejects_invalid_uri(semantic_id: str) -> None:
    with pytest.raises(ValueError, match="not a valid URI"):
        UriDictionaryEntry(semantic_id=semantic_id)
