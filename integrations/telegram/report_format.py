"""Render a scheduled report for Telegram.

Telegram HTML has bold, italics, links and code but no headings or lists, so
the report's shape (:mod:`infrastructure.text.report_layout`) is spelled out:
a bold title, the lead, each section under a bold heading with ``•`` bullets,
and an italic footer naming the loop. The text is then rendered by the same
Markdown-to-HTML converter as every other Telegram reply.
"""

from __future__ import annotations

from infrastructure.text.report_layout import ReportLine, parse_report
from integrations.telegram.formatting import markdown_to_telegram_html

_BULLET = "• "
_NESTED_BULLET = "    ◦ "


def telegram_report_html(markdown: str, *, loop_name: str = "") -> str:
    """Telegram-safe HTML for one scheduled report."""
    layout = parse_report(markdown)
    title = layout.title or loop_name.strip() or "Scheduled report"
    parts = [f"**{title}**"]
    lead = _lines(layout.lead.lines)
    if lead:
        parts.append(lead)
    for section in layout.sections:
        body = _lines(section.lines)
        parts.append(f"**{section.heading}**" + (f"\n{body}" if body else ""))
    if layout.note:
        parts.append(f"_{layout.note}_")
    name = loop_name.strip()
    footer = f"{name} · scheduled by OpenSRE" if name and name != title else "Scheduled by OpenSRE"
    parts.append(f"_{footer}_")
    return markdown_to_telegram_html("\n\n".join(parts))


def _lines(lines: tuple[ReportLine, ...]) -> str:
    out: list[str] = []
    code: list[str] = []
    for line in lines:
        if line.code:
            code.append(line.text)
            continue
        if code:
            out.append("```\n" + "\n".join(code) + "\n```")
            code = []
        if line.bullet:
            out.append((_NESTED_BULLET if line.depth else _BULLET) + line.text)
        else:
            out.append(line.text)
    if code:
        out.append("```\n" + "\n".join(code) + "\n```")
    return "\n".join(out).strip()


__all__ = ["telegram_report_html"]
