# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

from __future__ import annotations

import asyncio
import base64
import os
from collections.abc import Iterable
from typing import Final

import structlog
from asyncua.ua.uaerrors import BadInvalidState
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from opensmi.core import SkillState
from opensmi.core.errors import OpenSmiRuntimeError
from transitions import MachineError

from opensmi.server.interfaces import AbstractSkill

_LOGGER = structlog.get_logger("opensmi.common")


async def reset_skill_and_wait(skill: AbstractSkill, *, timeout: float | None = 60.0) -> None:
    """Reset the given skill and wait until it finished (successfully reached Ready state or Halted on failure).

    Note: This will only wait for the 'Halting' skill state, but not for other skill transition states!
    """
    # we need to wait for skills still in halting state to reach halted
    if skill.current_state == SkillState.HALTING:
        async with asyncio.timeout(timeout):
            await skill.wait_for_state(SkillState.HALTED)

    if skill.current_state in (SkillState.HALTED, SkillState.SUSPENDED, SkillState.COMPLETED):
        _LOGGER.debug("Resetting skill...", skill_name=skill.path)
        await skill.reset()
        # resetting takes time, we need to wait until the skill is actually ready!
        async with asyncio.timeout(timeout):
            await skill.wait_for_state(SkillState.READY)


async def halt_skill_and_wait(skill: AbstractSkill, *, timeout: float | None = 60.0) -> None:
    """Halt the given skill and wait until it finished and is in the Halted state."""
    try:
        _LOGGER.debug("Trying to halt skill...", skill_name=skill.path)
        await skill.halt()
        # halting takes time, we need to wait until the skill is actually halted!
        async with asyncio.timeout(timeout):
            await skill.wait_for_state(SkillState.HALTED)
    except (BadInvalidState, MachineError):
        pass  # assume that skill is already halted
    except (Exception, asyncio.CancelledError) as ex:
        msg = f"Could not halt skill '{skill.name}'!"
        raise OpenSmiRuntimeError(msg) from ex


async def reset_skills_parallel_and_wait(skills: Iterable[AbstractSkill], *, timeout: float | None = 60.0) -> None:
    """Reset all given skills in 'parallel'."""
    tasks = [asyncio.create_task(reset_skill_and_wait(skill)) for skill in skills]
    for coro in asyncio.as_completed(tasks, timeout=timeout):
        await coro  # may raise


async def halt_skills_parallel_and_wait(skills: Iterable[AbstractSkill], *, timeout: float | None = 60.0) -> None:
    """Halt all given skills in 'parallel'."""
    tasks = [asyncio.create_task(halt_skill_and_wait(skill)) for skill in skills]
    for coro in asyncio.as_completed(tasks, timeout=timeout):
        await coro  # may raise


SECRET_KEY_NAME: Final[str] = "OPEN_SMI_SECRET_KEY"
"""The name of the environment variable containing the secret key/pepper."""


def get_scrypt_instance() -> Scrypt:
    """Return initialized Scrypt instance.

    The pepper is read from base64 encoded environment variable.

    :raises RuntimeError: If either the pepper environment variable is missing or does not contain valid Base64.
    """
    try:
        pepper = base64.b64decode(os.environ[SECRET_KEY_NAME], validate=True)
        # We intentionally use a deployment-specific pepper as the Scrypt salt to
        # ensure embedded password hashes cannot be verified without the pepper.
        return Scrypt(salt=pepper, length=32, n=2**14, r=8, p=1)
    except KeyError:
        msg = f"{SECRET_KEY_NAME} environment variable is not set."
        raise RuntimeError(msg) from None
    except ValueError as err:
        msg = f"{SECRET_KEY_NAME} is not valid Base64."
        raise RuntimeError(msg) from err
