"""Keep repositories' AGENTS.md text out of prompts that leave the machine.

The action prompt's REPOSITORY INSTRUCTIONS block can carry a private
repository's AGENTS.md. Analytics events and trace exports of a system prompt
replace the body of each section that carries file text with one placeholder
line; the section's header line and every other character of the prompt stay
as they were. The local prompt log keeps the full text. A section cut off
before its closing wrapper (a truncated prompt) is left out to the end.
"""

from __future__ import annotations

import re

from config.constants.repository_instructions import (
    REPOSITORY_INSTRUCTIONS_CLOSE_TAG,
    REPOSITORY_INSTRUCTIONS_FILE_PREFIX,
    REPOSITORY_INSTRUCTIONS_HEADER_PREFIX,
)

#: The header line of a section that carries file text; repository names hold no comma.
_HEADER = re.compile(
    rf"^{re.escape(REPOSITORY_INSTRUCTIONS_HEADER_PREFIX)}(?P<repository>[^,\n]+),[^\n]*\):$",
    re.MULTILINE,
)
#: What follows such a header: an optional budget note line, then the first file's wrapper.
_BODY_START = re.compile(rf"\n(?:\[[^\n]*\]\n)?{re.escape(REPOSITORY_INSTRUCTIONS_FILE_PREFIX)}")
_NEXT_FILE = f"\n\n{REPOSITORY_INSTRUCTIONS_FILE_PREFIX}"


def omit_repository_instructions(text: str) -> str:
    """Return ``text`` with each REPOSITORY INSTRUCTIONS body replaced by a placeholder line.

    The placeholder names the repository and how many characters were left
    out. Text without such a section comes back unchanged.
    """
    parts: list[str] = []
    position = 0
    for header in _HEADER.finditer(text):
        # A header-like line inside a body already left out, or one not followed
        # by a file wrapper, is not a section.
        if header.start() < position or _BODY_START.match(text, header.end()) is None:
            continue
        body_start = header.end() + 1
        body_end = _section_end(text, body_start)
        parts.append(text[position:body_start])
        parts.append(
            "[REPOSITORY INSTRUCTIONS omitted from analytics: "
            f"{header['repository']}, {body_end - body_start} chars]"
        )
        position = body_end
    parts.append(text[position:])
    return "".join(parts)


def _section_end(text: str, start: int) -> int:
    """Where the section's last file wrapper closes, or the end of ``text`` when one is cut off."""
    end = start
    while True:
        close = text.find(REPOSITORY_INSTRUCTIONS_CLOSE_TAG, end)
        if close < 0:
            return len(text)
        end = close + len(REPOSITORY_INSTRUCTIONS_CLOSE_TAG)
        if not text.startswith(_NEXT_FILE, end):
            return end


__all__ = ["omit_repository_instructions"]
