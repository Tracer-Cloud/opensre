"""Markdown emphasis helpers shared by chat and terminal renderers."""

from __future__ import annotations

import re

_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
_ASTERISK_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")


def _protect_code(text: str) -> tuple[str, list[str]]:
    stash: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        stash.append(match.group(0))
        return f"\x00{len(stash) - 1}\x00"

    text = _CODE_BLOCK_RE.sub(_stash, text)
    text = _INLINE_CODE_RE.sub(_stash, text)
    return text, stash


def _restore_code(text: str, stash: list[str]) -> str:
    for index, original in enumerate(stash):
        text = text.replace(f"\x00{index}\x00", original)
    return text


def tighten_markdown_emphasis(text: str) -> str:
    """Collapse spaces inside ``**bold**`` so GFM/Slack/Discord actually render it.

    Models often emit ``** I found: **``; common Markdown drops bold when there
    is a space between the markers and the text. Code spans are left alone.
    """
    if not text:
        return text
    protected, stash = _protect_code(text)

    def _repl(match: re.Match[str]) -> str:
        inner = match.group(1).strip()
        return f"**{inner}**" if inner else ""

    protected = _ASTERISK_BOLD_RE.sub(_repl, protected)
    return _restore_code(protected, stash)


__all__ = ["tighten_markdown_emphasis"]
