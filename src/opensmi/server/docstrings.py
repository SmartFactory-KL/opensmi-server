# SPDX-FileCopyrightText: 2026 OpenSMI Contributors
#
# SPDX-License-Identifier: MIT

"""Python docstrings as OPC UA descriptions."""

import inspect
import re

_LIST_ITEM = re.compile(r"(?:[-*]|\d+\.) ")


def description_from_docstring(docstring: str | None) -> str | None:
    """Turn a docstring into an OPC UA description, which is Markdown.

    The indentation is removed and the lines of a paragraph are joined, so docstrings can be wrapped as usual and
    clients that show the text as is don't break it in the middle of a sentence. Paragraphs stay separated by a blank
    line and list items (``- ``, ``* `` or ``1. ``) stay on their own lines. Markup is kept.

    :return: The description, or ``None`` if the docstring is empty.
    """
    text = inspect.cleandoc(docstring or "")
    paragraphs = (_unwrap(paragraph) for paragraph in text.split("\n\n"))
    return "\n\n".join(paragraph for paragraph in paragraphs if paragraph) or None


def _unwrap(paragraph: str) -> str:
    """Join the wrapped lines of a paragraph; a list item starts a new line."""
    lines: list[str] = []
    for line in paragraph.splitlines():
        line = line.strip()  # noqa: PLW2901
        if not line:
            continue
        if lines and not _LIST_ITEM.match(line):
            lines[-1] = f"{lines[-1]} {line}"
        else:
            lines.append(line)
    return "\n".join(lines)
