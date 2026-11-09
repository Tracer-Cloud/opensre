"""Convert the agent's Markdown output to Slack mrkdwn.

Slack does not render standard Markdown: ``**bold**`` shows literal asterisks,
``#`` headings and ``[text](url)`` links are not recognized. This maps the
common Markdown the LLM emits onto Slack's mrkdwn so replies read cleanly.
"""

from __future__ import annotations

import re

from infrastructure.text.markdown import tighten_markdown_emphasis

_CODE_BLOCK_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]+`")
# ``**bold**`` always; ``__bold__`` only when it is not a dunder filename
# (``__init__.py``) — Slack would otherwise bold ``init`` and drop the underscores.
_ASTERISK_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_UNDERSCORE_BOLD_RE = re.compile(r"(?<![\w.])__(?!_)(.+?)__(?![\w.])")
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.*?)\s*#*\s*$")
_BULLET_RE = re.compile(r"^(\s*)[-*]\s+")


def _protect(text: str) -> tuple[str, list[str]]:
    """Replace code spans with placeholders so formatting rules skip them."""
    stash: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        stash.append(match.group(0))
        return f"\x00{len(stash) - 1}\x00"

    text = _CODE_BLOCK_RE.sub(_stash, text)
    text = _INLINE_CODE_RE.sub(_stash, text)
    return text, stash


def _restore(text: str, stash: list[str]) -> str:
    for index, original in enumerate(stash):
        text = text.replace(f"\x00{index}\x00", original)
    return text


def _convert_line(line: str) -> str:
    heading = _HEADING_RE.match(line)
    if heading:
        title = heading.group(1).strip()
        if not title:
            return ""
        # Leave existing ``**title**`` for the bold pass; wrap plain titles
        # the same way so headings and bold share one conversion.
        if title.startswith("**") and title.endswith("**") and len(title) >= 4:
            return title
        return f"**{title}**"
    return _BULLET_RE.sub(lambda m: f"{m.group(1)}• ", line)


def _slack_bold(inner: str) -> str:
    text = inner.strip()
    return f"*{text}*" if text else ""


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Return ``text`` with common Markdown rewritten as Slack mrkdwn."""
    if not text:
        return text

    # Tight ``**bold**`` first so a padded ``** I found: **`` does not become
    # a ``* `` bullet after conversion (Slack treats ``* text`` as a list).
    protected, stash = _protect(tighten_markdown_emphasis(text))

    # Links: [label](url) -> <url|label>
    protected = _LINK_RE.sub(lambda m: f"<{m.group(2)}|{m.group(1)}>", protected)
    # Headings and bullets before bold, so ``*bold*`` is never parsed as a list.
    protected = "\n".join(_convert_line(line) for line in protected.split("\n"))
    protected = _ASTERISK_BOLD_RE.sub(lambda m: _slack_bold(m.group(1)), protected)
    protected = _UNDERSCORE_BOLD_RE.sub(lambda m: _slack_bold(m.group(1)), protected)

    return _restore(protected, stash)
