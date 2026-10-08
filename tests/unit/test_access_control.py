# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT
import asyncio
import base64
import os
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio
from asyncua import ua
from asyncua.common.utils import ServiceError
from asyncua.server.internal_server import InternalServer
from asyncua.server.internal_session import InternalSession

from opensmi.server import AccessControl
from opensmi.server.common import SECRET_KEY_NAME, get_scrypt_instance
from opensmi.server.config import AccessControlConfiguration, UserConfiguration
from opensmi.server.user import encode_password

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def access_control() -> AccessControl:
    """Access control instance with default configuration."""
    return AccessControl(config=AccessControlConfiguration())


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def call_get_user(
    access_control: AccessControl,
    session: InternalSession | None = None,
    iserver: InternalServer | None = None,
    username: str | None = None,
    password: str | bytes | None = None,
):
    """Call ``get_user`` from a frame that has a local named ``self`` (mimics ``InternalSession``).

    ``get_user`` grabs ``self`` via currentframe().f_back.f_locals["self"].
    """
    if session is None:
        session = make_session()
    self = session  # noqa: F841
    return access_control.get_user(
        iserver=iserver or MagicMock(spec=InternalServer),
        username=username,
        password=password,
    )


def make_session(name=("10.0.0.1", 4840)):
    session = MagicMock(spec=InternalSession)  # passes isinstance(session, InternalSession)
    session.name = name
    return session


def status(exc_info):
    return exc_info.value.code


# ---------------------------------------------------------------------------
# tests
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize(("username", "password"), [(None, "pw"), ("operator", None), (None, None)])
async def test_missing_credentials_raises_invalid(access_control, username, password) -> None:
    with pytest.raises(ServiceError) as exc:
        call_get_user(access_control, make_session(), username=username, password=password)
    assert status(exc) == ua.status_codes.StatusCodes.BadIdentityTokenInvalid


@pytest.mark.asyncio
async def test_unknown_user_returns_none(access_control):
    assert call_get_user(access_control, username="does_not_exist", password="pw") is None


@pytest.mark.asyncio
async def test_wrong_plaintext_password_returns_none(access_control) -> None:
    assert call_get_user(access_control, username="visitor", password="wrong_password") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("password", ["visitor", b"visitor"])
async def test_correct_plaintext_password_returns_correct_user(access_control, password) -> None:
    user = call_get_user(access_control, username="visitor", password=password)
    assert user is not None
    assert user.name == "visitor"


@pytest.mark.asyncio
async def test_missing_pepper_env_raises(monkeypatch) -> None:
    monkeypatch.delenv(SECRET_KEY_NAME, raising=False)

    access_control = AccessControl(
        config=AccessControlConfiguration(
            users=[
                UserConfiguration(
                    name="visitor",
                    password=encode_password(b"visitor"),  # unpeppered
                    priority=0,
                    maximum_access_level=0,
                )
            ]
        )
    )
    with pytest.raises(RuntimeError, match="environment variable is not set"):
        call_get_user(access_control, username="visitor", password=b"wrong_password")
    with pytest.raises(RuntimeError, match="environment variable is not set"):
        call_get_user(access_control, username="visitor", password="wrong_password")


@pytest.mark.asyncio
async def test_with_peppered_password(monkeypatch) -> None:
    monkeypatch.setenv(SECRET_KEY_NAME, base64.b64encode(os.urandom(16)).decode("ascii"))

    access_control = AccessControl(
        config=AccessControlConfiguration(
            users=[
                UserConfiguration(
                    name="visitor",
                    password=encode_password(get_scrypt_instance().derive(b"visitor")),
                    priority=0,
                    maximum_access_level=0,
                )
            ]
        )
    )
    assert call_get_user(access_control, username="visitor", password=b"wrong_password") is None
    assert call_get_user(access_control, username="visitor", password="wrong_password") is None
    assert call_get_user(access_control, username="visitor", password=b"visitor") is not None
    assert call_get_user(access_control, username="visitor", password="wrong_password") is None


@pytest.mark.asyncio
async def test_new_connection_adds_session(access_control) -> None:
    session = make_session()

    user = call_get_user(access_control, session, username="visitor", password="visitor")
    assert user is not None
    user._ua_is_present = AsyncMock()
    session.user = user

    await asyncio.sleep(0)
    assert session in access_control.sessions
