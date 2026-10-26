"""The shape of a scheduled report, read from the Markdown a loop's reply is written in.

A loop's reply is one portable Markdown document (see
``infrastructure.scheduling.scheduler.report_style``): an optional ``#`` title, a short
lead, then ``##`` sections of bullets and short paragraphs. Every delivery
channel renders that same shape in its own format -- Slack Block Kit,
Telegram HTML, an email -- so the parsing lives here, vendor-neutral, and
each channel only decides how a title, a section and a line look.

Parsing is forgiving because models drift: a line that is nothing but bold
(``**Needs action**``) or ends in a colon and is bold (``**GitHub:**``) is a
section heading too, and a reply with no headings at all is one lead block.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

_ATX_HEADING = re.compile(r"^\s{0,3}(#{1,6})\s+(?P<title>.*?)\s*#*\s*$")
_BOLD_ONLY_LINE = re.compile(r"^\s*(?:\*\*|__)(?P<title>[^*_\n]{1,80}?)(?:\*\*|__)\s*:?\s*$")
_FENCE = re.compile(r"^\s*```")
_RULE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
_ITALIC_ONLY_LINE = re.compile(r"^\s*_(?P<text>[^_\n]+)_\s*$")
_BULLET = re.compile(r"^(?P<indent>\s*)(?:[-*+•]|\d{1,2}[.)])\s+(?P<text>.*)$")

#: Longest heading kept as a heading; longer "headings" are prose a model bolded.
_MAX_HEADING_CHARS = 80


@dataclass(frozen=True, slots=True)
class ReportLine:
    """One line of a section: a bullet (with its nesting depth) or a paragraph line."""

    text: str
    bullet: bool = False
    depth: int = 0
    code: bool = False


@dataclass(frozen=True, slots=True)
class ReportSection:
    """A headed part of the report; ``heading`` is "" for the lead."""

    heading: str
    lines: tuple[ReportLine, ...] = ()
    #: The heading was a bold-only line (``**GitHub**``), not a ``##`` heading.
    from_bold: bool = False

    def is_empty(self) -> bool:
        return not any(line.text.strip() for line in self.lines)


@dataclass(frozen=True, slots=True)
class ReportLayout:
    """A report's title, lead and sections, in reading order."""

    title: str = ""
    lead: ReportSection = field(default_factory=lambda: ReportSection(""))
    sections: tuple[ReportSection, ...] = ()
    #: A closing italic line (``_Coverage: …_``): shown quietly, apart from the body.
    note: str = ""

    def summary(self) -> str:
        """The first meaningful line: what a notification preview should say."""
        for section in (self.lead, *self.sections):
            for line in section.lines:
                if line.text.strip() and not line.code:
                    return line.text.strip()
        return self.title


def parse_report(markdown: str) -> ReportLayout:
    """Split a report's Markdown into its title, lead and sections."""
    title = ""
    sections: list[ReportSection] = []
    heading = ""
    from_bold = False
    lines: list[ReportLine] = []
    in_fence = False

    def close() -> None:
        sections.append(ReportSection(heading, tuple(_trim(lines)), from_bold))

    for raw in (markdown or "").replace("\r\n", "\n").split("\n"):
        if _FENCE.match(raw):
            in_fence = not in_fence
            continue
        if in_fence:
            lines.append(ReportLine(raw.rstrip(), code=True))
            continue
        if _RULE.match(raw):
            continue
        found = _heading(raw)
        if found is not None:
            level, text, bold = found
            if level == 1 and not title and not sections and not _has_text(lines):
                title = text
                continue
            close()
            heading, from_bold, lines = text, bold, []
            continue
        bullet = _BULLET.match(raw)
        if bullet:
            depth = len(bullet.group("indent").replace("\t", "  ")) // 2
            lines.append(ReportLine(bullet.group("text").strip(), bullet=True, depth=min(depth, 2)))
        else:
            lines.append(ReportLine(raw.strip()))
    close()

    note = _pop_note(sections)
    sections = _keep_bold_statements(sections)
    lead = sections[0] if sections and not sections[0].heading else ReportSection("")
    rest = tuple(section for section in sections if section.heading and not section.is_empty())
    return ReportLayout(title=title, lead=lead, sections=rest, note=note)


def _keep_bold_statements(sections: list[ReportSection]) -> list[ReportSection]:
    """A bold-only line with nothing under it is a statement, not a heading.

    ``**Deploys all health-checked.**`` closing a report would otherwise be read
    as a heading with an empty body and dropped. A real ``##`` heading without
    a body is still dropped. The statement joins the section before it (or the
    lead) as a bold line.
    """
    kept: list[ReportSection] = []
    for section in sections:
        if section.heading and section.is_empty() and section.from_bold:
            line = ReportLine(f"**{section.heading}**")
            if kept:
                previous = kept[-1]
                kept[-1] = ReportSection(previous.heading, (*previous.lines, line))
            else:
                kept.append(ReportSection("", (line,)))
            continue
        kept.append(section)
    return kept


def _pop_note(sections: list[ReportSection]) -> str:
    """Take a trailing italic-only line off the last section, as the report's note."""
    if not sections or not sections[-1].lines:
        return ""
    last = sections[-1]
    match = _ITALIC_ONLY_LINE.match(last.lines[-1].text) if not last.lines[-1].bullet else None
    if match is None or last.lines[-1].code:
        return ""
    sections[-1] = ReportSection(last.heading, tuple(_trim(list(last.lines[:-1]))))
    return match.group("text").strip()


def _heading(line: str) -> tuple[int, str, bool] | None:
    """``(level, text, from_bold)`` for a heading line, else ``None``."""
    atx = _ATX_HEADING.match(line)
    if atx:
        text = _strip_emphasis(atx.group("title"))
        return (len(atx.group(1)), text, False) if text else None
    bold = _BOLD_ONLY_LINE.match(line)
    if bold:
        text = bold.group("title").strip().rstrip(":").strip()
        if text and len(text) <= _MAX_HEADING_CHARS:
            return 2, text, True
    return None


def _strip_emphasis(text: str) -> str:
    text = text.strip()
    for marker in ("**", "__"):
        if text.startswith(marker) and text.endswith(marker) and len(text) > 2 * len(marker):
            text = text[len(marker) : -len(marker)].strip()
    return text.rstrip(":").strip()


def _has_text(lines: list[ReportLine]) -> bool:
    return any(line.text.strip() for line in lines)


def _trim(lines: list[ReportLine]) -> list[ReportLine]:
    """Drop blank lines at both ends and collapse runs of blank lines to one."""
    kept: list[ReportLine] = []
    for line in lines:
        if not line.text.strip() and not line.code:
            if kept and kept[-1].text.strip():
                kept.append(ReportLine(""))
            continue
        kept.append(line)
    while kept and not kept[-1].text.strip() and not kept[-1].code:
        kept.pop()
    return kept


__all__ = ["ReportLayout", "ReportLine", "ReportSection", "parse_report"]
