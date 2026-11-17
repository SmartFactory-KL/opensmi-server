# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Server specific errors."""

from opensmi.core.errors import OpenSmiError


class TypeConflictError(OpenSmiError, ValueError):
    """A type cannot be created or bound: its name is taken by a different definition, or the class has another."""
