# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import base64
import os

import pytest
from _pytest.monkeypatch import MonkeyPatch
from cryptography.exceptions import InvalidKey

from opensmi.server.common import SECRET_KEY_NAME, get_scrypt_instance


def test_verifies_password_derived_with_same_pepper(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_KEY_NAME, base64.b64encode(os.urandom(16)).decode("ascii"))

    password = b"secret"
    derived = get_scrypt_instance().derive(password)

    get_scrypt_instance().verify(password, derived)


def test_rejects_password_when_it_does_not_match(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_KEY_NAME, base64.b64encode(os.urandom(16)).decode("ascii"))

    derived = get_scrypt_instance().derive(b"secret")

    with pytest.raises(InvalidKey):
        get_scrypt_instance().verify(b"wrong", derived)


def test_raises_when_pepper_is_not_configured(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.delenv(SECRET_KEY_NAME, raising=False)

    with pytest.raises(RuntimeError, match="environment variable is not set"):
        _ = get_scrypt_instance()
