# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""OPC UA dictionary entries (semantic identifiers) as defined in OPC 10000-19.

An entry pairs a ``semantic_id`` (its meaning) with an optional human-readable name.
Use `IrdiDictionaryEntry` or `UriDictionaryEntry` depending on the identifier format.
Validation is syntactic only; ids are not looked up in any dictionary.
"""

import re
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import ClassVar, final
from urllib.parse import urlparse

from asyncua import ua

_REGEX_IEC_CDD = re.compile(r"0112/2//.+#[A-Z0-9]+#[A-Z0-9]+")
_REGEX_ISO = re.compile(r"0112-1-.+#[A-Z0-9]+#[a-zA-Z0-9]+")
_REGEX_ECLASS = re.compile(r"0173/1///#\d{2}-[A-Z0-9]+#[a-zA-Z0-9]+")

_IRDI_PATTERNS = (_REGEX_IEC_CDD, _REGEX_ISO, _REGEX_ECLASS)


@dataclass(frozen=True, slots=True)
class DictionaryEntry(ABC):
    """Abstract OPC UA dictionary entry (OPC 10000-19).

    Subclasses define the identifier format by setting the class variables and
    implementing `_validate`. Instances are immutable and validated on creation.

    See also https://reference.opcfoundation.org/specs/OPC-10000-19
    """

    ua_namespace_uri: ClassVar[str]
    """Namespace URI of the dictionary; instances of this entry type live in this namespace."""
    ua_type_node_id: ClassVar[ua.NodeId]
    """Node ID of the OPC UA type representing this entry type."""

    semantic_id: str
    """Identifier defining the meaning (semantic); used as OPC UA browse name."""
    name: str | None = None
    """Optional human-readable name; used as OPC UA display name."""

    @final
    def __post_init__(self) -> None:
        """Validate `semantic_id` via `_validate`."""
        self._validate()

    @abstractmethod
    def _validate(self) -> None:
        """Check `semantic_id` against the entry type's format.

        :raises ValueError: If `semantic_id` is not valid for this entry type.
        """


@dataclass(frozen=True, slots=True)
class IrdiDictionaryEntry(DictionaryEntry):
    """Dictionary entry identified by an IRDI (International Registration Data Identifier).

    IRDIs are defined in ISO/IEC 11179-6. Accepted formats: IEC CDD, ISO and ECLASS.
    """

    ua_type_node_id: ClassVar[ua.NodeId] = ua.NodeId(ua.Int32(ua.object_ids.ObjectIds.IrdiDictionaryEntryType))
    ua_namespace_uri: ClassVar[str] = "http://opcfoundation.org/UA/Dictionary/IRDI"

    def _validate(self) -> None:
        if not any(pattern.fullmatch(self.semantic_id) for pattern in _IRDI_PATTERNS):
            msg = f"{self.semantic_id!r} is not a valid IRDI!"
            raise ValueError(msg)


@dataclass(frozen=True, slots=True)
class UriDictionaryEntry(DictionaryEntry):
    """Dictionary entry identified by a URI (Uniform Resource Identifier)."""

    ua_type_node_id: ClassVar[ua.NodeId] = ua.NodeId(ua.Int32(ua.object_ids.ObjectIds.UriDictionaryEntryType))
    ua_namespace_uri: ClassVar[str] = "http://opcfoundation.org/UA/Dictionary/URI"

    def _validate(self) -> None:
        if any(char.isspace() for char in self.semantic_id):
            msg = f"{self.semantic_id!r} is not a valid URI, it contains whitespace!"
            raise ValueError(msg)

        parse_result = urlparse(self.semantic_id)
        if not parse_result.scheme:
            msg = f"{self.semantic_id!r} is not a valid URI, scheme is missing!"
            raise ValueError(msg)
