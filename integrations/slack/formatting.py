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
_LINK_RE = re.compile(r"\[([^\]]+)\]\((https?://)")
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


def _convert_links(text: str) -> str:
    """Convert HTTP(S) links with balanced destination parentheses."""
    parts: list[str] = []
    cursor = search_from = 0
    while match := _LINK_RE.search(text, search_from):
        end = match.end()
        depth = 0
        while end < len(text):
            char = text[end]
            if char.isspace():
                break
            if char == "(":
                depth += 1
            elif char == ")":
                if depth == 0:
                    break
                depth -= 1
            end += 1
        if end < len(text) and text[end] == ")" and depth == 0 and end > match.end():
            parts.append(text[cursor : match.start()])
            url = text[match.start(2) : end]
            parts.append(f"<{url}|{match.group(1)}>")
            cursor = end + 1
            search_from = cursor
        else:
            # Only recover a following link once this destination has failed.
            search_from = match.end()
    parts.append(text[cursor:])
    return "".join(parts)


def markdown_to_slack_mrkdwn(text: str) -> str:
    """Return ``text`` with common Markdown rewritten as Slack mrkdwn."""
    if not text:
        return text

    # Tight ``**bold**`` first so a padded ``** I found: **`` does not become
    # a ``* `` bullet after conversion (Slack treats ``* text`` as a list).
    protected, stash = _protect(tighten_markdown_emphasis(text))

    # Links: [label](url) -> <url|label>
    protected = _convert_links(protected)
    # Headings and bullets before bold, so ``*bold*`` is never parsed as a list.
    protected = "\n".join(_convert_line(line) for line in protected.split("\n"))
    protected = _ASTERISK_BOLD_RE.sub(lambda m: _slack_bold(m.group(1)), protected)
    protected = _UNDERSCORE_BOLD_RE.sub(lambda m: _slack_bold(m.group(1)), protected)

    return _restore(protected, stash)
