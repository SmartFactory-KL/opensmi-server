# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

# Note: Due to Python import conflict, this file is named `_server.py` instead of `server.py`
import asyncio
import sys
import time
import warnings
from collections.abc import AsyncGenerator, Iterator
from dataclasses import dataclass
from enum import IntEnum, auto
from importlib.resources import files
from pathlib import Path
from typing import TYPE_CHECKING, Self

import asyncua
import structlog
from asyncua import ua
from asyncua.common.node import Node
from asyncua.common.structures104 import new_enum
from asyncua.crypto.cert_gen import generate_private_key, generate_self_signed_app_certificate
from asyncua.server.address_space import AddressSpace
from asyncua.server.history_sql import HistorySQLite
from asyncua.server.server import Server as UaServer
from opensmi.core import AsyncTaskMixin, LifecycleState, setup_logging
from opensmi.core.base_server import BaseServer
from opensmi.core.lifecycle_mixin import LifecycleMixin, lifecycle
from opensmi.core.protocols import NamespaceProvider
from opensmi.core.repr_mixin import ReprStrMixin
from opensmi.core.ua_node_util import get_child_without_ns
from typing_extensions import TypeVar, override

from opensmi.server.config import HistoryOption, ServerConfiguration, UaServerConfiguration
from opensmi.server.nodesets import NODE_SETS, SmartFactoryMachineSetNodeIds
from opensmi.server.ua_object import UaObject

if TYPE_CHECKING:
    from opensmi.server import BaseMachine
    from opensmi.server.access_control import AccessControl

_MachineType = TypeVar("_MachineType", bound="BaseMachine", default="BaseMachine")


class _FixedHistorySQLite(HistorySQLite):
    @override
    def _get_table_name(self, node_id: ua.NodeId) -> str:
        if node_id.NodeIdType == ua.NodeIdType.String:
            return f"{node_id.NamespaceIndex}_{node_id.Identifier}"  # repr creates invalid ' character
        return f"{node_id.NamespaceIndex}_{node_id.Identifier!r}"


def _create_key_and_certificate(config: UaServerConfiguration) -> None:
    from cryptography import x509
    from cryptography.hazmat._oid import ExtendedKeyUsageOID
    from cryptography.hazmat.primitives import serialization

    # Generate new key
    key = generate_private_key()
    with open(config.private_key, "wb") as f:
        f.write(
            key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    # Generate new self-signed certificate based on that key
    cert = generate_self_signed_app_certificate(
        private_key=key,
        common_name=config.name,
        names={},
        subject_alt_names=[x509.UniformResourceIdentifier(config.namespace_uri)],
        extended=[ExtendedKeyUsageOID.SERVER_AUTH, ExtendedKeyUsageOID.CLIENT_AUTH],
        days=3650,
    )

    with open(config.certificate, "wb") as f:
        f.write(cert.public_bytes(serialization.Encoding.DER))


@dataclass(slots=True, frozen=True)
class NodesetReference:
    """A pointer to an XML asset. Resolved lazily via ``importlib.resources``."""

    package: str
    """The package the nodeset lives in."""
    namespace: type[NamespaceProvider]
    """The namespace provider of the nodeset."""


class ServerState(IntEnum):
    """Operating states of the `Server`."""

    STOPPED = auto()
    STARTING = auto()
    RUNNING = auto()
    STOPPING = auto()


class Server(
    AsyncTaskMixin,
    LifecycleMixin,
    ReprStrMixin,
    BaseServer[UaObject],
):
    _ua_machines: Node

    _access_control: AccessControl
    _ua_server: UaServer
    _ua_address_space: AddressSpace

    def __init__(self, *, config_path: str | Path | None = None) -> None:
        super().__init__()

        self._machines: list[BaseMachine] = []
        self._state = ServerState.STOPPED
        self._ua_enums: dict[type[IntEnum], ua.NodeId] = {}
        """Maps created custom enums to OPC UA type nodes."""
        self._ua_nodesets_xml: dict[str, NodesetReference] = {}
        """Maps nodeset XML URIs to information how to load the corresponding XML file."""
        if config_path:
            self.load_config(config_path)
        else:
            self._config: ServerConfiguration = ServerConfiguration()

    def load_config(self, path: str | Path) -> None:
        """Load configuration from given ``path``."""
        if self.lifecycle_state != LifecycleState.NEW:
            msg = "Cannot configure server after initialization!"
            raise RuntimeError(msg)

        from opensmi.core.config import load_dataclass

        self._config = load_dataclass(ServerConfiguration, path)

    @lifecycle
    async def _life_cycle(self) -> AsyncGenerator[None]:
        await self._init()
        yield
        await self._shutdown()

    async def _init(self) -> None:
        import opensmi.core

        import opensmi.server

        setup_logging(log_levels=self.config.logging.minimum_levels, log_format=self.config.logging.format)
        self._logger = structlog.stdlib.get_logger("opensmi.Server")
        self.logger.info(
            "Initializing server...",
            version_opensmi_server=opensmi.server.__version__,
            version_opensmi_core=opensmi.core.__version__,
            version_asyncua=asyncua.__version__,
        )

        from .access_control import AccessControl, AccessControlAttributeService, AccessControlMethodService

        self._access_control = AccessControl(server=self)

        self._ua_server = UaServer(user_manager=self._access_control)
        ua_iserver = self.ua_server.iserver
        self._ua_address_space = ua_iserver.aspace

        ua_iserver.method_service = AccessControlMethodService(ua_iserver.aspace)
        ua_iserver.attribute_service = AccessControlAttributeService(aspace=ua_iserver.aspace, server=self)

        await self._setup_ua_server()

        # register our nodesets
        for namespace in NODE_SETS.values():
            self.ua_register_nodeset_xml(namespace=namespace, package="opensmi.server.nodesets")

        # Import Nodeset (and its required nodesets)
        await self.ua_import_nodeset(SmartFactoryMachineSetNodeIds)

        self._ua_machines = await get_child_without_ns(self._ua_server.nodes.objects, display_name="Machines")

    @property
    def logger(self) -> structlog.stdlib.BoundLogger:
        """Return the logger instance of the server. Read-only property."""
        return self._logger

    @property
    def access_control(self) -> AccessControl:
        return self._access_control

    def ua_register_nodeset_xml(self, *, namespace: type[NamespaceProvider], package: str) -> None:
        """Register the given nodeset XML file living in `package`. Overrides existing nodesets based on URI."""
        self._ua_nodesets_xml[namespace.URI] = NodesetReference(
            package=package,
            namespace=namespace,
        )
        self.logger.debug("Registered nodeset XML", package=package, uri=namespace.URI)

    async def ua_import_xml(self, *, path: Path | None = None, xml_string: str | None = None) -> int:
        """Import XML nodeset from given ``path``. Returns the namespace index on success."""
        time_start = time.monotonic()
        nodes = await self.ua_server.import_xml(
            path=path,  # pyright: ignore[reportArgumentType]
            xmlstring=xml_string,
        )
        idx = nodes[0].NamespaceIndex
        namespace_array = await self.ua_server.get_namespace_array()
        uri: str = namespace_array[idx]
        self.logger.info(
            "Imported nodes from XML node set!",
            uri=uri,
            ns_idx=idx,
            no_of_nodes=len(nodes),
            elapsed=time.monotonic() - time_start,
        )
        return idx

    async def ua_import_nodeset(self, uri: str | type[NamespaceProvider]) -> int:
        """Import the OPC UA XML nodeset specified by the given ``uri`` and return the namespace index.

        Automatically loads all required nodesets beforehand.

        :raises KeyError: If given URI is unknown.
        """
        if not isinstance(uri, str):
            uri = uri.URI

        index = self._namespace_map.get(uri)
        if index is not None:
            return index

        ref: NodesetReference = self._ua_nodesets_xml[uri]
        for uri in ref.namespace.REQUIRED_URIS:  # import all requirements first (transitive)
            await self.ua_import_nodeset(uri)

        xml_string = files(ref.package).joinpath(ref.namespace.FILE_NAME).read_text(encoding="utf-8")
        index = await self.ua_import_xml(xml_string=xml_string)

        # update our namespace mapping
        for ns_idx, namespace in enumerate(await self.ua_server.get_namespace_array()):
            self._namespace_map[namespace] = ns_idx

        return index

    async def _setup_ua_server(self) -> None:
        config: UaServerConfiguration = self.config.ua_server
        # Configure server to use sqlite as history database (default is a simple memory dict)
        if config.history_db == HistoryOption.SQLITE:
            db_path = Path("./data/opcua_history.sql")
            db_path.parent.mkdir(exist_ok=True)
            self._ua_server.iserver.history_manager.set_storage(_FixedHistorySQLite(db_path))  # pyright: ignore[reportArgumentType]
            self.logger.info("History storage via SQLite!", path=str(db_path))

        if config.encryption:
            self.logger.info("Encryption enabled!")
            try:
                await self._ua_server.load_certificate(config.certificate)
                await self._ua_server.load_private_key(config.private_key)
                self.logger.info("Loaded existing encryption key and certificate!")
            except FileNotFoundError:
                self.logger.info("Creating new encryption key and certificate...")
                _create_key_and_certificate(config)

                await self._ua_server.load_certificate(config.certificate)
                await self._ua_server.load_private_key(config.private_key)

        await self._ua_server.init()

        if self.config.debug:
            self.logger.warning("DEBUG mode enabled!")
            asyncio.get_event_loop().set_debug(True)
            warnings.simplefilter("always", ResourceWarning)
            warnings.simplefilter("always", DeprecationWarning)

        self._ua_server.set_endpoint(config.endpoint_address)
        self._ua_server.set_server_name(config.name)
        await self._ua_server.set_application_uri(config.namespace_uri)  # always namespace index 1

        # TODO? SecurityPolicy "None" shall be disabled when Encryption is available according to OPC UA spec
        #  https://profiles.opcfoundation.org/profile/762
        if not config.encryption:
            self._ua_server.set_security_policy([ua.SecurityPolicyType.NoSecurity])
        self._ua_server.set_identity_tokens([ua.UserNameIdentityToken])

    async def add_machine(self, machine: _MachineType) -> _MachineType:
        """Add given machine to the server. Will initialize the machine if not yet initialized."""
        if not self.is_initialized:
            await self.init()

        assert machine is not None

        time_start = time.monotonic()
        if not machine.is_initialized:
            machine.server = self  # pyright: ignore[reportAttributeAccessIssue]
            await machine.ua_create_node(self._ua_machines)
            await machine.init()

        self._machines.append(machine)
        self.logger.info(
            "Added machine.",
            machine_name=machine.name,
            elapsed=time.monotonic() - time_start,
        )
        return machine

    @property
    def state(self) -> ServerState:
        """Read-only. Return the state of the server."""
        return self._state

    @property
    def machines(self) -> list[BaseMachine]:
        """Read-only. Return all machines added to the server."""
        return list(self._machines)

    @property
    def ua_server(self) -> UaServer:
        """Read-only. Return the internal OPC UA server instance."""
        return self._ua_server  # exists after instantiation

    @property
    def ua_address_space(self) -> AddressSpace:
        """Read-only. Return the internal OPC UA address space instance."""
        return self._ua_address_space

    @property
    def config(self) -> ServerConfiguration:
        """Return the server-wide configuration. Read-only property."""
        if not hasattr(self, "_config") or self._config is None:
            msg = "Server is not yet configured! Call configure() first."
            raise RuntimeError(msg)

        return self._config

    async def _register_server(self, retry_seconds: float = 10.0):
        lds_client = asyncua.client.client.Client(self.config.discovery.server_address)
        while self._state == ServerState.RUNNING:
            try:
                await lds_client.register_server(self._ua_server)
            except Exception:
                try:
                    await lds_client.connect()
                except Exception:
                    self.logger.exception(
                        f"Failed to register to discovery server, retrying in {retry_seconds} seconds.",
                        discovery_server_address=self.config.discovery.server_address,
                    )
            await asyncio.sleep(retry_seconds)

    async def start(self, blocking: bool = True) -> None:
        """Start the internal OPC UA server.

        Server must be initialized beforehand, and must be in state `STOPPED`.

        :param blocking If True, block until server shutdown or KeyboardInterrupt is caught
        """
        if not self.is_initialized:
            msg = "Server is not initialized!"
            raise RuntimeError(msg)
        if self.state != ServerState.STOPPED:
            msg = "Server is not stopped!"
            raise RuntimeError(msg)

        self._state = ServerState.STARTING
        await self._ua_server.start()
        for endpoint in await self._ua_server.get_endpoints():
            self.logger.info(
                "Serving OPC UA server at:",
                endpoint_url=endpoint.EndpointUrl,
                security_policy_uri=endpoint.SecurityPolicyUri,
            )
        self._state = ServerState.RUNNING

        if self.config.discovery.register:
            self._create_task(self._register_server(), name="DiscoveryRegisterTask")
        else:
            self.logger.warning("Register at Discovery server is disabled!")

        if blocking:
            try:
                await self._watchdog_loop()
            except asyncio.CancelledError:
                self.logger.info("Received CancelledError, exiting endless loop!")
            except KeyboardInterrupt:
                self.logger.info("Received KeyboardInterrupt, exiting endless loop!")
        else:
            self._create_task(self._watchdog_loop(), name="WatchdogTask")

    async def _watchdog_loop(
        self,
        *,
        interval: float = 0.2,
        warning_threshold: float = 0.1,
    ) -> None:
        """Monitor how busy the event loop is and warn if it is probably too busy."""
        loop = asyncio.get_running_loop()
        deadline: float = loop.time() + interval

        while self.running:
            await asyncio.sleep(max(0, deadline - loop.time()))

            now = loop.time()
            lag = now - deadline

            if lag >= warning_threshold:
                self.logger.warning(
                    "Event loop lag detected",
                    lag=round(lag, 3),
                    threshold=warning_threshold,
                )

            deadline = now + interval

    async def _shutdown(self) -> None:
        self._state = ServerState.STOPPING

        for machine in self.machines:
            self.logger.debug("Trying to call shutdown()...", machine_name=machine.name)
            try:
                await machine.shutdown()
            except (Exception, asyncio.CancelledError):  # shutdown() is implemented by user, might raise some exception
                self.logger.exception("Error during machine shutdown!", machine_name=machine.name)

        self.access_control.running = False
        await self._cancel_tasks()
        await self.ua_server.stop()

        self._state = ServerState.STOPPED

    @property
    def running(self) -> bool:
        """Whether the server is running."""
        return self._state == ServerState.RUNNING

    @property
    def stopped(self) -> bool:
        """Whether the server is stopped."""
        return self._state == ServerState.STOPPED

    async def get_enum(self, enum_type: type[IntEnum], *, option_set: bool = False) -> ua.NodeId:
        """Return OPC UA Node representation of given enum type."""
        try:
            return self._ua_enums[enum_type]
        except KeyError:
            ua_node = await new_enum(
                server=self.ua_server,
                idx=1,
                name=enum_type.__name__,  # type: ignore
                fields=[enum_type(index).name for index in enum_type],
                option_set=option_set,
            )
            self._ua_enums[enum_type] = ua_node.nodeid
            self.logger.info("Created new enum type", name=enum_type.__name__, ua_node_id=str(ua_node))
            return ua_node.nodeid

    def __del__(self) -> None:
        if self.lifecycle_state in (
            LifecycleState.INITIALIZING,
            LifecycleState.INITIALIZED,
            LifecycleState.SHUTTING_DOWN,
        ):
            # Note: At this point, we cannot (async) shut down properly anymore
            # try to warn the user to prevent the same mistake next time
            print(
                f"Server was not properly shut down! life_cycle={self.lifecycle_state.name}",
                file=sys.stderr,
                flush=True,
            )

    async def __aenter__(self) -> Self:
        """Enter the asynchronous context manager; no setup required."""
        await self.init()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        """Exit the asynchronous context manager and shut down the server."""
        await self.shutdown()

    @override
    def _repr_items(self) -> Iterator[tuple[str, object]]:
        yield from super()._repr_items()
        yield "state", self._state.name
        yield "life_cycle_state", self.lifecycle_state.name
