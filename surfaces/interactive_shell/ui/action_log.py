"""Grouped, collapsible tool-action log rendered above the closing reply.

Tool calls are buffered per turn and flushed here. By default a TTY shows
nothing: the full call + result detail is stashed for Ctrl+O only. With
``/verbose on`` (``TRACER_VERBOSE``) the calls render as bordered sections, one
per run of same-kind calls (all ``GitHub CLI`` calls together, etc.), each
showing concise status lines — never the inline ``key: value ·`` arguments.
"""

from __future__ import annotations

from collections.abc import Iterator

from rich.console import Console, Group
from rich.text import Text

from infrastructure.observability.render.debug import verbose_output_enabled
from infrastructure.terminal.theme import DIM, SECONDARY
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.session.terminal_session import ActionLogEntry
from surfaces.interactive_shell.ui.transcript import (
    TranscriptRole,
    compact_transcript_prefix,
    transcript_prefix,
)
from surfaces.shared.terminal.components.rendering import print_repl_renderable, repl_output_width
from surfaces.shared.terminal.prompt_layout import terminal_columns

_H = "─"
_V = "│"
_TL = "╭"
_TR = "╮"
_BL = "╰"
_BR = "╯"
#: Never let the box exceed the terminal; keep a small right margin.
_BOX_MARGIN = 2
#: Floor so a short section still reads as a box, not a stub.
_MIN_INNER = 12
# Keep enough of the tool identity visible to distinguish narrow rows.
_MIN_SINGLE_ROW_BODY_WIDTH = 7
#: Only draw a box once this many same-kind calls run back to back; a lone call
#: reads as a single dim line, not a one-row box.
_MIN_GROUP_FOR_BOX = 2
#: Below this many columns a box cannot hold a command; the calls print as rows.
_MIN_BOX_WIDTH = _MIN_INNER + 4


def flush_action_log(console: Console, session: Session) -> None:
    """Flush the turn's buffered tool calls; clear the buffer.

    On a TTY the calls stay hidden unless verbose output is on: their detail is
    stashed for Ctrl+O and nothing is printed. Verbose renders grouped sections.
    Off a TTY (gateway/logs) the full detail is printed inline so nothing is
    lost where Ctrl+O does not exist. A no-op when the turn recorded no calls.

    The rows go out in one buffered CRLF write: the flush lands while the
    prompt's raw stdout patch is active, where row-by-row ``\n`` output
    starts every next row where the previous one ended.
    """
    if not session.terminal.has_action_log():
        return
    entries = session.terminal.take_action_log()

    if not console.is_terminal:
        for entry in entries:
            console.print(Text(entry.detail or entry.kind, style=str(DIM)))
        return

    if not verbose_output_enabled():
        _stash_hidden(session, entries)
        return

    # Rows are sized to the width the buffered writer renders at. Sizing them
    # to the terminal instead made every row one cell too wide there, so each
    # wrapped: blank lines between rows and the corners on their own lines.
    width = _box_width(console)
    rows: list[Text] = [Text("")]
    for group in _group_by_kind(entries):
        if len(group) >= _MIN_GROUP_FOR_BOX and width >= _MIN_BOX_WIDTH:
            rows.extend(_section_rows(session, group, width=width))
        else:
            rows.extend(_single_row(session, entry, width=width) for entry in group)
    print_repl_renderable(console, Group(*rows))


def _stash_hidden(session: Session, entries: list[ActionLogEntry]) -> None:
    """Keep the turn's call detail reachable via Ctrl+O without printing a row."""
    detail = "\n\n".join(entry.detail for entry in entries if entry.detail)
    if detail:
        session.terminal.stash_collapsed_tool_output(detail)


def _box_width(console: Console) -> int:
    """Row width: the writer's render width, never past the window's last column."""
    return min(repl_output_width(console), max(1, terminal_columns() - 1))


def _group_by_kind(entries: list[ActionLogEntry]) -> Iterator[list[ActionLogEntry]]:
    """Yield runs of consecutive entries that share a ``kind``."""
    group: list[ActionLogEntry] = []
    for entry in entries:
        if group and entry.kind != group[-1].kind:
            yield group
            group = []
        group.append(entry)
    if group:
        yield group


def _single_row(session: Session, entry: ActionLogEntry, *, width: int) -> Text:
    """A lone tool call as one dim line; its detail is stashed for Ctrl+O."""
    if entry.detail:
        session.terminal.stash_collapsed_tool_output(entry.detail)
    label = f"{entry.kind} · {entry.concise}" if entry.concise else entry.kind
    max_width = max(_MIN_INNER, width - _BOX_MARGIN)
    prefix = transcript_prefix(TranscriptRole.TOOL)
    if max_width - len(prefix) < _MIN_SINGLE_ROW_BODY_WIDTH:
        prefix = compact_transcript_prefix(TranscriptRole.TOOL)
    line = f"{prefix}{label}"
    if len(line) > max_width:
        line = line[: max_width - 1] + "…"
    row = Text()
    row.append(line[: len(prefix)], style=str(SECONDARY))
    row.append(line[len(prefix) :], style=str(DIM))
    return row


def _section_rows(session: Session, group: list[ActionLogEntry], *, width: int) -> list[Text]:
    """One full-box section (title in the top border); its detail is stashed."""
    kind = group[0].kind
    count = len(group)
    header = kind if count == 1 else f"{kind} · {count} actions"
    body = [entry.concise for entry in group if entry.concise]
    detail = "\n\n".join(entry.detail for entry in group if entry.detail)
    if detail:
        session.terminal.stash_collapsed_tool_output(detail)
        body.append("Ctrl+O to expand details")

    # Span the render width, matching the input composer plate (the border
    # and padding claim 4 cells).
    inner = max(_MIN_INNER, width - 4)

    def _clip(text: str) -> str:
        return text if len(text) <= inner else text[: inner - 1] + "…"

    title = _clip(header)
    top_fill = _H * max(0, inner - 1 - len(title))
    rows = [Text(f"{_TL}{_H} {title} {top_fill}{_TR}", style=str(SECONDARY))]
    for line in body:
        cell = _clip(line)
        rows.append(Text(f"{_V} {cell}{' ' * (inner - len(cell))} {_V}", style=str(DIM)))
    rows.append(Text(f"{_BL}{_H * (inner + 2)}{_BR}", style=str(DIM)))
    return rows
