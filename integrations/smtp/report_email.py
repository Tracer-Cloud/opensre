"""Render a scheduled report as an email: a subject, plain text and HTML.

Mail clients ignore ``<style>`` blocks and most CSS, so the HTML is a single
narrow column with inline styles only, built from the report's shape
(:mod:`infrastructure.text.report_layout`): the title as the heading and the
subject, the lead as the opening paragraph, each section under its own
heading with real lists and links. The plain-text part carries the same
content for clients that do not render HTML.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass

from infrastructure.text.report_layout import ReportLine, parse_report

_LINK = re.compile(r"\[([^\]]+)\]\((https?://[^)\s]+)\)")
_BOLD = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
_ITALIC = re.compile(r"(?<![\w*])[_*](?![_*\s])(.+?)(?<![_*\s])[_*](?![\w*])")
_CODE = re.compile(r"`([^`\n]+)`")
_SLACK_LINK = re.compile(r"<(https?://[^|>\s]+)\|([^>]+)>")
_SUBJECT_LIMIT = 120

_FONT = "-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif"
_STYLE = {
    "body": f"margin:0;padding:24px 16px;background:#f6f7f9;font-family:{_FONT};color:#1f2328;",
    "card": (
        "max-width:640px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;"
        "border-radius:8px;padding:28px 28px 20px;"
    ),
    "title": "margin:0 0 12px;font-size:20px;line-height:28px;font-weight:600;color:#111827;",
    "lead": "margin:0 0 8px;font-size:15px;line-height:24px;color:#1f2328;",
    "heading": (
        "margin:24px 0 8px;padding-top:16px;border-top:1px solid #eef0f3;font-size:13px;"
        "line-height:18px;font-weight:600;letter-spacing:.04em;text-transform:uppercase;"
        "color:#4b5563;"
    ),
    "p": "margin:0 0 8px;font-size:14px;line-height:22px;",
    "ul": "margin:0 0 8px;padding-left:20px;",
    "li": "margin:0 0 6px;font-size:14px;line-height:22px;",
    "a": "color:#0b57d0;text-decoration:underline;",
    "code": (
        "font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;"
        "background:#f3f4f6;border-radius:4px;padding:1px 4px;"
    ),
    "pre": (
        "font-family:SFMono-Regular,Menlo,Consolas,monospace;font-size:12px;line-height:18px;"
        "background:#f3f4f6;border-radius:6px;padding:10px 12px;white-space:pre-wrap;margin:0 0 8px;"
    ),
    "footer": "margin:24px 0 0;font-size:12px;line-height:18px;color:#6b7280;",
}


@dataclass(frozen=True, slots=True)
class ReportEmail:
    """One report, ready for a multipart message."""

    subject: str
    text: str
    html: str


def render_report_email(markdown: str, *, loop_name: str = "") -> ReportEmail:
    """The subject, plain text and HTML for one scheduled report."""
    layout = parse_report(_markdown_links(markdown))
    title = layout.title or loop_name.strip() or "Scheduled report"
    footer = f"Sent by OpenSRE · {loop_name.strip()}" if loop_name.strip() else "Sent by OpenSRE"

    html_parts = [f'<h1 style="{_STYLE["title"]}">{_inline_html(title)}</h1>']
    html_parts.extend(_lines_html(layout.lead.lines, lead=True))
    text_parts = [_inline_text(title), "=" * min(len(_inline_text(title)), 60), ""]
    text_parts.extend(_lines_text(layout.lead.lines))
    for section in layout.sections:
        html_parts.append(f'<h2 style="{_STYLE["heading"]}">{_inline_html(section.heading)}</h2>')
        html_parts.extend(_lines_html(section.lines))
        text_parts.extend(["", _inline_text(section.heading).upper(), *_lines_text(section.lines)])
    if layout.note:
        note_style = _STYLE["p"] + "color:#6b7280;font-style:italic;margin-top:16px;"
        html_parts.append(f'<p style="{note_style}">{_inline_html(layout.note)}</p>')
        text_parts.extend(["", _inline_text(layout.note)])
    html_parts.append(f'<p style="{_STYLE["footer"]}">{html.escape(footer)}</p>')
    text_parts.extend(["", "--", footer])

    body = "".join(html_parts)
    document = (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1"></head>'
        f'<body style="{_STYLE["body"]}"><div style="{_STYLE["card"]}">{body}</div></body></html>'
    )
    subject = _inline_text(title)
    if len(subject) > _SUBJECT_LIMIT:
        subject = subject[: _SUBJECT_LIMIT - 1].rstrip() + "…"
    return ReportEmail(subject=subject, text="\n".join(text_parts).strip() + "\n", html=document)


def _markdown_links(text: str) -> str:
    """A reply written for Slack (``<url|label>``) reads as Markdown links here."""
    return _SLACK_LINK.sub(lambda match: f"[{match.group(2)}]({match.group(1)})", text or "")


def _lines_html(lines: tuple[ReportLine, ...], *, lead: bool = False) -> list[str]:
    out: list[str] = []
    items: list[str] = []
    code: list[str] = []

    def flush() -> None:
        if items:
            out.append(f'<ul style="{_STYLE["ul"]}">' + "".join(items) + "</ul>")
            items.clear()
        if code:
            out.append(f'<pre style="{_STYLE["pre"]}">' + html.escape("\n".join(code)) + "</pre>")
            code.clear()

    paragraph = _STYLE["lead"] if lead else _STYLE["p"]
    for line in lines:
        if line.code:
            if items:
                flush()
            code.append(line.text)
            continue
        if code:
            flush()
        if line.bullet:
            indent = "margin-left:16px;" if line.depth else ""
            items.append(f'<li style="{_STYLE["li"]}{indent}">{_inline_html(line.text)}</li>')
        elif line.text:
            flush()
            out.append(f'<p style="{paragraph}">{_inline_html(line.text)}</p>')
        else:
            flush()
    flush()
    return out


def _lines_text(lines: tuple[ReportLine, ...]) -> list[str]:
    out: list[str] = []
    for line in lines:
        if line.code:
            out.append(f"    {line.text}")
        elif line.bullet:
            out.append(("    - " if line.depth else "- ") + _inline_text(line.text))
        else:
            out.append(_inline_text(line.text))
    return out


def _inline_html(text: str) -> str:
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"\x00{len(stash) - 1}\x00"

    text = _CODE.sub(
        lambda m: keep(f'<code style="{_STYLE["code"]}">{html.escape(m.group(1))}</code>'), text
    )
    text = _LINK.sub(
        lambda m: keep(
            f'<a href="{html.escape(m.group(2), quote=True)}" style="{_STYLE["a"]}">'
            f"{html.escape(m.group(1))}</a>"
        ),
        text,
    )
    escaped = html.escape(text)
    escaped = _BOLD.sub(lambda m: f"<strong>{m.group(1) or m.group(2)}</strong>", escaped)
    escaped = _ITALIC.sub(lambda m: f"<em>{m.group(1)}</em>", escaped)
    for index, fragment in enumerate(stash):
        escaped = escaped.replace(f"\x00{index}\x00", fragment)
    return escaped


def _inline_text(text: str) -> str:
    text = _LINK.sub(lambda m: f"{m.group(1)} ({m.group(2)})", text)
    text = _BOLD.sub(lambda m: m.group(1) or m.group(2), text)
    text = _ITALIC.sub(lambda m: m.group(1), text)
    return _CODE.sub(lambda m: m.group(1), text).strip()


__all__ = ["ReportEmail", "render_report_email"]
