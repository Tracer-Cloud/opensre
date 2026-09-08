"""Grouped, collapsible tool-action log rendered above the closing reply.

Tool calls are buffered per turn and flushed here as bordered sections, one per
run of same-kind calls (all ``GitHub CLI`` calls together, etc.). Each section
shows concise status lines — never the inline ``key: value ·`` arguments — and
stashes the full call + result detail for Ctrl+O. The log reads as a secondary
execution record beneath the reply, the way Cursor and Droid render tool chrome.
"""

from __future__ import annotations

from collections.abc import Iterator

from rich.console import Console, Group
from rich.text import Text

from infrastructure.terminal.theme import DIM, SECONDARY
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.session.terminal_session import ActionLogEntry
from surfaces.shared.terminal.components.rendering import print_repl_renderable
from surfaces.shared.terminal.prompt_layout import terminal_columns

_H = "─"
_V = "│"
_TL = "╭"
_TR = "╮"
_BL = "╰"
_BR = "╯"
_MARKER = "⏺"
#: Never let the box exceed the terminal; keep a small right margin.
_BOX_MARGIN = 2
#: Floor so a short section still reads as a box, not a stub.
_MIN_INNER = 12
#: Only draw a box once this many same-kind calls run back to back; a lone call
#: reads as a single dim line, not a one-row box.
_MIN_GROUP_FOR_BOX = 2


def flush_action_log(console: Console, session: Session) -> None:
    """Flush the turn's buffered tool calls as grouped sections; clear the buffer.

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

    rows: list[Text] = [Text("")]
    for group in _group_by_kind(entries):
        if len(group) >= _MIN_GROUP_FOR_BOX:
            rows.extend(_section_rows(session, group))
        else:
            rows.append(_single_row(session, group[0]))
    print_repl_renderable(console, Group(*rows))


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


def _single_row(session: Session, entry: ActionLogEntry) -> Text:
    """A lone tool call as one dim line; its detail is stashed for Ctrl+O."""
    if entry.detail:
        session.terminal.stash_collapsed_tool_output(entry.detail)
    label = f"{entry.kind} · {entry.concise}" if entry.concise else entry.kind
    line = f"{_MARKER} {label}"
    max_width = max(_MIN_INNER, terminal_columns() - _BOX_MARGIN)
    if len(line) > max_width:
        line = line[: max_width - 1] + "…"
    return Text(line, style=str(DIM))


def _section_rows(session: Session, group: list[ActionLogEntry]) -> list[Text]:
    """One full-box section (title in the top border); its detail is stashed."""
    kind = group[0].kind
    count = len(group)
    header = kind if count == 1 else f"{kind} · {count} actions"
    body = [entry.concise for entry in group if entry.concise]
    detail = "\n\n".join(entry.detail for entry in group if entry.detail)
    if detail:
        session.terminal.stash_collapsed_tool_output(detail)
        body.append("Ctrl+O to expand details")

    # Span the full window, matching the input composer plate (total box width
    # == terminal columns; the border/padding claim 4 cells).
    inner = max(_MIN_INNER, terminal_columns() - 4)

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
