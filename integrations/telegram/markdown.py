"""Convert agent Markdown and Slack mrkdwn into Telegram ``parse_mode=HTML``.

Telegram HTML supports a small tag subset (``<b> <i> <u> <s> <code> <pre> <a>``).
This module is the single source of truth for turning mixed Markdown / Slack text
into safe HTML. Callers should always send with a plain-text fallback when the API
rejects markup.
"""

from __future__ import annotations

import html
import re

_INLINE_CODE = re.compile(r"`([^`\n]+)`")
_MD_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^\s)]+)\)")
_SLACK_LINK = re.compile(r"<(https?://[^|>]+)(?:\|([^>]+))?>")
# Alternation order matters: ``**``/``__`` spans win over Slack single-star bold.
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__|(?<!\*)\*([^*\n]+)\*(?!\*)")
_ITALIC = re.compile(r"(?<![\w_])_([^_\n]+?)_(?![\w_])")
_PLACEHOLDER = re.compile("\x00(\\d+)\x00")
_HEADER = re.compile(r"^#{1,6}\s+(.+)$")
_HRULE = re.compile(r"^(\*{3,}|-{3,}|_{3,})\s*$")
_LIST_ITEM = re.compile(r"^(\d+\.|[-*+•])\s+(.+)$")
_TELEGRAM_TAG = re.compile(r"<(?:b|i|u|s|code|pre|a)\b", re.IGNORECASE)


def looks_like_telegram_html(text: str) -> bool:
    """Return True when *text* already contains Telegram HTML markup."""
    return bool(_TELEGRAM_TAG.search(text))


def render_markdown_as_telegram_html(text: str) -> str:
    """Convert mixed Markdown / Slack mrkdwn into Telegram-safe HTML."""
    lines = text.splitlines()
    out: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip().startswith("```"):
            index += 1
            start = index
            while index < len(lines) and not lines[index].strip().startswith("```"):
                index += 1
            code = "".join(f"{code_line}\n" for code_line in lines[start:index])
            out.append(f"<pre>{html.escape(code, quote=False)}</pre>")
            index += 1  # skip the closing fence (harmless past EOF)
        elif _is_table_row(lines[index]):
            rows: list[list[str]] = []
            while index < len(lines) and _is_table_row(lines[index]):
                rows.append([cell.strip() for cell in lines[index].strip()[1:-1].split("|")])
                index += 1
            out.extend(_table_lines(rows))
        else:
            out.append(_render_line(lines[index]))
            index += 1

    merged: list[str] = []
    for part in out:  # collapse blank runs left by rules and skipped rows
        if part or not merged or merged[-1]:
            merged.append(part)
    return "\n".join(merged)


def _render_line(line: str) -> str:
    if header := _HEADER.match(line):
        return f"<b>{html.escape(header.group(1).strip())}</b>"
    if _HRULE.match(line.strip()):
        return ""
    if item := _LIST_ITEM.match(line):
        marker = item.group(1) if item.group(1).endswith(".") else "•"
        return f"{marker} {_render_inline(item.group(2))}"
    return _render_inline(line)


def _render_inline(line: str) -> str:
    stash: list[str] = []

    def keep(rendered: str) -> str:
        stash.append(rendered)
        return f"\x00{len(stash) - 1}\x00"

    def link(label: str, url: str) -> str:
        safe_label = label.replace("|", "¦").strip() or url
        return f'<a href="{html.escape(url, quote=True)}">{html.escape(safe_label)}</a>'

    def bold_html(match: re.Match[str], rendered: str) -> str:
        underscore_inner = match.group(2)
        if underscore_inner is not None and rendered[match.end() : match.end() + 1] == ".":
            # ``__init__.py`` is a filename, not Markdown bold.
            return match.group(0)
        inner = (match.group(1) or underscore_inner or match.group(3) or "").strip()
        if not inner:
            return ""
        return keep(f"<b>{html.escape(inner, quote=False)}</b>")

    # Stash rendered spans behind placeholders so escaping can't touch them.
    text = _INLINE_CODE.sub(
        lambda m: keep(f"<code>{html.escape(m.group(1), quote=False)}</code>"), line
    )
    text = _SLACK_LINK.sub(lambda m: keep(link(m.group(2) or m.group(1), m.group(1))), text)
    text = _MD_LINK.sub(lambda m: keep(link(m.group(1), m.group(2))), text)
    text = _BOLD.sub(lambda m: bold_html(m, text), text)
    text = html.escape(text, quote=False)
    text = _ITALIC.sub(r"<i>\1</i>", text)
    return _PLACEHOLDER.sub(lambda m: stash[int(m.group(1))], text)


def _is_table_row(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and "|" in stripped[1:-1]


def _table_lines(rows: list[list[str]]) -> list[str]:
    """Render table body rows (header dropped) as mobile-friendly bullets."""
    lines: list[str] = []
    for row in rows[1:]:
        cells = [cell for cell in row if cell]
        if not cells or all(set(cell) <= {"-", ":", " "} for cell in cells):
            continue  # separator row
        head, *tail = (html.escape(cell) for cell in cells)
        joined = " · ".join(tail)
        lines.append(f"• <b>{head}</b> — {joined}" if joined else f"• {head}")
    return lines


__all__ = ["looks_like_telegram_html", "render_markdown_as_telegram_html"]
