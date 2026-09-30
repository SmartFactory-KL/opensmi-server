# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import pytest

from opensmi.server import Server
from opensmi.server.config import ServerConfiguration


@pytest.fixture
def server() -> Server:
    """Return an uninitialized `Server` instance."""
    config = ServerConfiguration()
    config.ua_server.endpoint_address = "opc.tcp://0.0.0.0:0/server"
    return Server(config=config)
