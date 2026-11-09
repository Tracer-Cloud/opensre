"""Render a scheduled report as Slack Block Kit.

A loop's reply is portable Markdown (:mod:`infrastructure.text.report_layout`).
Posted as one ``text`` field it reads as a wall: ``**bold**`` and
``[label](url)`` show literally, headings are lost, and Slack unfurls every
link into a preview card. As blocks it reads like a report: a header, the
one-line lead, each section under a divider, and a quiet context footer that
says which loop sent it. ``text`` stays as the notification fallback.
"""

from __future__ import annotations

import re
from typing import Any

from infrastructure.text.report_layout import ReportLayout, ReportLine, ReportSection, parse_report
from integrations.slack.formatting import markdown_to_slack_mrkdwn

#: Slack's limits: header text, section text, blocks per message, fallback text.
_HEADER_LIMIT = 150
_SECTION_LIMIT = 3000
_BLOCK_LIMIT = 50
_FALLBACK_LIMIT = 300
_MARKDOWN_LINK = re.compile(r"\[([^\]]+)\]\([^)\s]+\)")
_EMPHASIS = re.compile(r"\*\*|__|`")
_NESTED_BULLET = " ◦ "
_BULLET = "• "


def report_blocks(markdown: str, *, loop_name: str = "") -> tuple[str, list[dict[str, Any]]]:
    """Return ``(fallback_text, blocks)`` for a scheduled report.

    ``loop_name`` heads the message when the report has no ``#`` title, and is
    named in the footer otherwise, so a reader always knows what sent it.
    """
    layout = parse_report(markdown)
    title = layout.title or loop_name.strip() or "Scheduled report"
    blocks: list[dict[str, Any]] = [_header(title)]
    if not layout.lead.is_empty():
        blocks.extend(_sections(_render_lines(layout.lead.lines)))
    for section in layout.sections:
        blocks.append({"type": "divider"})
        blocks.extend(_sections(_render_section(section)))
    blocks = _fit(blocks)
    if layout.note:
        note = markdown_to_slack_mrkdwn(layout.note)
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": f"_{note}_"}]})
    footer = _footer(layout, loop_name)
    if footer:
        blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]})
    return _fallback(title, layout), blocks


def _header(title: str) -> dict[str, Any]:
    text = _plain(title)
    if len(text) > _HEADER_LIMIT:
        text = text[: _HEADER_LIMIT - 1].rstrip() + "…"
    return {"type": "header", "text": {"type": "plain_text", "text": text, "emoji": True}}


def _render_section(section: ReportSection) -> str:
    heading = markdown_to_slack_mrkdwn(f"**{section.heading}**")
    body = _render_lines(section.lines)
    return f"{heading}\n{body}" if body else heading


def _render_lines(lines: tuple[ReportLine, ...]) -> str:
    out: list[str] = []
    code: list[str] = []

    def flush_code() -> None:
        if code:
            out.append("```\n" + "\n".join(code) + "\n```")
            code.clear()

    for line in lines:
        if line.code:
            code.append(line.text)
            continue
        flush_code()
        if not line.text:
            out.append("")
        elif line.bullet:
            prefix = _NESTED_BULLET if line.depth else _BULLET
            out.append(prefix + _inline(line.text))
        else:
            out.append(_inline(line.text))
    flush_code()
    return "\n".join(out).strip()


def _inline(text: str) -> str:
    """Bold, links and code for one line (its bullet marker is already removed)."""
    return markdown_to_slack_mrkdwn(text)


def _sections(text: str) -> list[dict[str, Any]]:
    """One or more section blocks, split at line breaks under Slack's 3,000 limit."""
    if not text.strip():
        return []
    chunks: list[str] = []
    current = ""
    for line in text.split("\n"):
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= _SECTION_LIMIT:
            current = candidate
            continue
        if current:
            chunks.append(current)
        while len(line) > _SECTION_LIMIT:
            chunks.append(line[:_SECTION_LIMIT])
            line = line[_SECTION_LIMIT:]
        current = line
    if current.strip():
        chunks.append(current)
    return [{"type": "section", "text": {"type": "mrkdwn", "text": chunk}} for chunk in chunks]


def _fit(blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Keep within Slack's block limit, leaving room for the footer."""
    room = _BLOCK_LIMIT - 2
    if len(blocks) <= room:
        return blocks
    kept = blocks[: room - 1]
    kept.append(
        {
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": "_Report shortened to fit Slack._"}],
        }
    )
    return kept


def _footer(layout: ReportLayout, loop_name: str) -> str:
    name = loop_name.strip()
    if not name:
        return "Scheduled by OpenSRE"
    if layout.title and layout.title.strip().lower() != name.lower():
        return f"{_plain(name)} · scheduled by OpenSRE"
    return "Scheduled by OpenSRE"


def _fallback(title: str, layout: ReportLayout) -> str:
    summary = layout.summary()
    text = f"{_plain(title)}: {summary}" if summary and summary != title else _plain(title)
    text = markdown_to_slack_mrkdwn(text)
    return text if len(text) <= _FALLBACK_LIMIT else text[: _FALLBACK_LIMIT - 1].rstrip() + "…"


def _plain(text: str) -> str:
    """Header text is plain: keep link labels, drop emphasis and code marks."""
    text = _MARKDOWN_LINK.sub(lambda match: match.group(1), text)
    return _EMPHASIS.sub("", text).strip()


__all__ = ["report_blocks"]
