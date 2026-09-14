# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import base64
import binascii
import os

import pytest
from _pytest.monkeypatch import MonkeyPatch

from opensmi.server import User
from opensmi.server.common import SECRET_KEY_NAME, get_scrypt_instance
from opensmi.server.config import UserConfiguration

PASSWORD_PLAINTEXT = "secret"


@pytest.fixture
def user_plain() -> User:
    return User(config=UserConfiguration(name="test", password=PASSWORD_PLAINTEXT, priority=0, maximum_access_level=2))


@pytest.fixture
def user_encrypted(monkeypatch: MonkeyPatch) -> User:
    monkeypatch.setenv(SECRET_KEY_NAME, base64.b64encode(os.urandom(16)).decode("ascii"))

    derived = get_scrypt_instance().derive(PASSWORD_PLAINTEXT.encode("utf-8"))

    password = f"base64:{base64.b64encode(derived).decode()}"
    return User(config=UserConfiguration(name="test", password=password, priority=0, maximum_access_level=2))


def test_sets_current_access_level_larger_than_max(user_plain) -> None:
    with pytest.raises(ValueError, match="level"):
        user_plain.current_access_level = 100


def test_sets_current_access_level_smaller_than_max(user_plain) -> None:
    user_plain.current_access_level = 1
    assert user_plain.current_access_level == 1


def test_sets_current_access_level_equal_max(user_plain) -> None:
    user_plain.current_access_level = user_plain.maximum_access_level  # equal
    assert user_plain.current_access_level == user_plain.maximum_access_level


def test_plaintext_password_correct(user_plain) -> None:
    assert user_plain.check_password("secret")
    assert not user_plain.check_password(b"secret")  # only plain str version is accepted


def test_plaintext_password_incorrect(user_plain) -> None:
    assert not user_plain.check_password("wrong")
    assert not user_plain.check_password(b"wrong")
    assert not user_plain.check_password(None)


def test_encrypted_password_correct(user_encrypted) -> None:
    assert user_encrypted.check_password(b"secret")
    assert not user_encrypted.check_password("secret")  # only bytes version is accepted


def test_encrypted_password_incorrect(user_encrypted) -> None:
    assert not user_encrypted.check_password(b"wrong")
    assert not user_encrypted.check_password("wrong")
    assert not user_encrypted.check_password(None)


def test_encrypted_password_no_scrypt() -> None:
    user = User(
        config=UserConfiguration(
            name="test",
            password="base64:QHi0XvPR9ug7FitG/yEjbD/ecGkyUd928Znl0SKoRT0=",  # = secret, but peppered
            priority=0,
            maximum_access_level=2,
        )
    )
    with pytest.raises(RuntimeError, match="OPEN_SMI_SECRET_KEY environment variable is not set"):
        assert not user.check_password(b"secret")
    with pytest.raises(RuntimeError, match="OPEN_SMI_SECRET_KEY environment variable is not set"):
        assert not user.check_password(b"wrong")

    assert not user.check_password(None)  # does not need it, instantly rejected
    assert not user.check_password("secret")


def test_invalid_base64_encoded_password() -> None:
    with pytest.raises(binascii.Error):
        User(
            config=UserConfiguration(
                name="test",
                password="base64:invalid",  # = secret, but peppered
                priority=0,
                maximum_access_level=2,
            )
        )


def test_invalid_peppered_password(monkeypatch: MonkeyPatch) -> None:
    monkeypatch.setenv(SECRET_KEY_NAME, base64.b64encode(os.urandom(16)).decode("ascii"))

    user = User(
        config=UserConfiguration(
            name="test",
            password="base64:QHi0XvPR9ug7FitG/yEjbD/ecGkyUd928Znl0SKoRT0=",  # = secret, but peppered
            priority=0,
            maximum_access_level=2,
        )
    )

    assert not user.check_password(b"secret")
    assert not user.check_password("secret")
    assert not user.check_password(b"wrong")
    assert not user.check_password("wrong")
    assert not user.check_password(None)
