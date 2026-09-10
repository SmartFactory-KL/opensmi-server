# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Configuration data classes and functions to load them from TOML files."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from opensmi.core import LogFormat


class HistoryOption(Enum):
    """Backend to use for OPC UA historic data access."""

    MEMORY = "memory"
    SQLITE = "sqlite"


@dataclass(slots=True, kw_only=True)
class DiscoveryConfiguration:
    """OPC UA discovery related configuration."""

    register: bool = False
    server_address: str = "opc.tcp://localhost:4840"


@dataclass(slots=True, kw_only=True)
class LoggingConfiguration:
    """Logging related configuration."""

    format: LogFormat = LogFormat.AUTOMATIC
    minimum_levels: dict[str, str] = field(
        default_factory=lambda: {
            "open_smi.Server": "INFO",
            "open_smi.Module": "WARNING",
            "open_smi.Component": "WARNING",
            "open_smi.Port": "WARNING",
            "open_smi.Axis": "WARNING",
            "open_smi.SafetyState": "WARNING",
            "open_smi.Gripper": "WARNING",
            "open_smi.PowerTrain": "WARNING",
            "open_smi.Controller": "WARNING",
            "open_smi.Software": "WARNING",
            "open_smi.TaskControl": "WARNING",
            "open_smi.Motor": "WARNING",
            "open_smi.MotionDevice": "WARNING",
            "open_smi.MotionDeviceSystem": "WARNING",
            "open_smi.Laser": "WARNING",
            "open_smi.Resource": "WARNING",
            "open_smi.Storage": "WARNING",
            "open_smi.StorageSlot": "WARNING",
            "open_smi.Safety": "WARNING",
            "open_smi.Shuttle": "WARNING",
            "open_smi.Skill": "WARNING",
            "open_smi.AccessControl": "INFO",
            "open_smi.Lock": "INFO",
            "open_smi.UaFiniteStateMachine": "ERROR",
            "asyncua": "ERROR",
        }
    )


@dataclass(slots=True, kw_only=True)
class UaServerConfiguration:
    """OPC UA server related configuration."""

    name: str = "UNNAMED OpenSMI Server"
    namespace_uri: str = "urn:smartfactory.de:machine:XYZ"  # unique(!) application URI
    """URI of namespace index 1 (machine-specifics)"""
    endpoint_address: str = "opc.tcp://0.0.0.0:4841/server"

    encryption: bool = False
    certificate: str = "server_certificate.der"
    private_key: str = "server_private_key.pem"

    history_db: HistoryOption = HistoryOption.MEMORY


@dataclass(slots=True, kw_only=True)
class UserConfiguration:
    """`User` (role) related configuration."""

    name: str
    """Name of the user."""

    password: str
    """ The password of the user. Cleartext or peppered and base64-encoded when prefixed with ``base64:``"""

    priority: int
    """ Priority of the user, used when breaking the lock. Higher priority users can break the lock of lower 
    priority users. """

    maximum_access_level: int
    """ Maximum access level of the user. All actions (interacting with skills, writing variables, etc.) require a 
    certain access level. Higher is better. """

    allow_multiple: bool = False
    """ Are multiple logins from different clients allowed? """


@dataclass(slots=True, kw_only=True)
class AccessControlConfiguration:
    """`AccessControl` related configuration."""

    users: list[UserConfiguration] = field(
        default_factory=lambda: [
            UserConfiguration(
                name="visitor", password="visitor", priority=0, maximum_access_level=0, allow_multiple=True
            ),
            UserConfiguration(
                name="planner", password="planner", priority=5, maximum_access_level=5, allow_multiple=False
            ),
            UserConfiguration(
                name="orchestrator", password="orchestrator", priority=10, maximum_access_level=10, allow_multiple=False
            ),
            UserConfiguration(
                name="remote", password="remote", priority=15, maximum_access_level=15, allow_multiple=False
            ),
            UserConfiguration(
                name="operator", password="operator", priority=20, maximum_access_level=20, allow_multiple=False
            ),
            UserConfiguration(
                name="watchdog", password="watchdog", priority=25, maximum_access_level=20, allow_multiple=False
            ),
            UserConfiguration(
                name="developer", password="developer", priority=100, maximum_access_level=100, allow_multiple=False
            ),
        ]
    )
    max_inactive_lock_time_milliseconds: int = 10 * 60 * 1000  # 10 minutes before lock must be renewed
    minimum_access_level: int = 1
    allow_reconnection_from_same_host: bool = True


@dataclass(slots=True, kw_only=True)
class ServerConfiguration:
    """`Server` related configuration."""

    debug: bool = False
    logging: LoggingConfiguration = field(default_factory=LoggingConfiguration)
    ua_server: UaServerConfiguration = field(default_factory=UaServerConfiguration)
    discovery: DiscoveryConfiguration = field(default_factory=DiscoveryConfiguration)
    access_control: AccessControlConfiguration = field(default_factory=AccessControlConfiguration)
