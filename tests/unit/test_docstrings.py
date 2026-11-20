# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

import pytest

from opensmi.server.docstrings import description_from_docstring


@pytest.mark.parametrize(
    ("docstring", "expected"),
    [
        (None, None),
        ("", None),
        ("   \n  ", None),
        ("One line.", "One line."),
        ("Wrapped over\n    two lines.", "Wrapped over two lines."),
        ("First paragraph,\n    wrapped.\n\n    Second paragraph.", "First paragraph, wrapped.\n\nSecond paragraph."),
        (
            "Steps:\n\n    - first\n    - second, wrapped\n      over two lines",
            "Steps:\n\n- first\n- second, wrapped over two lines",
        ),
        ("Items:\n    * one\n    * two", "Items:\n* one\n* two"),
        ("Order:\n    1. first\n    2. second", "Order:\n1. first\n2. second"),
        ("Markdown is *kept*,\n    `as is`.", "Markdown is *kept*, `as is`."),
    ],
)
def test_description_from_docstring(docstring: str | None, expected: str | None) -> None:
    assert description_from_docstring(docstring) == expected
