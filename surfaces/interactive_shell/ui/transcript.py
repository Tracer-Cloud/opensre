"""Aligned semantic labels for interactive-shell transcript rows.

Rows go to scrollback, which the terminal reflows on a width change, so they
must carry no trailing padding — see :mod:`infrastructure.terminal.markdown`.
``Table.grid`` pads each cell out to its column width, so the gutter renders
its body with ``pad=False`` and trims what is left via :func:`trim_row_padding`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from rich.segment import Segment
from rich.text import Text

from infrastructure.terminal.markdown import trim_row_padding

if TYPE_CHECKING:
    from rich.console import Console, ConsoleOptions, RenderableType, RenderResult


class TranscriptRole(StrEnum):
    """Visible markers used to distinguish transcript rows."""

    USER = "❱"
    ASSISTANT = "●"
    WORKING = "Working"
    TOOL = "Tool"
    ERROR = "Error"


# Status rows share a body column, while marker rows keep the compact gutter.
_STATUS_GUTTER_WIDTH = 9
_MARKER_GUTTER_WIDTH = 2
_MARKER_ROLES = frozenset({TranscriptRole.USER, TranscriptRole.ASSISTANT})


def _gutter_width(role: TranscriptRole) -> int:
    """Return the gutter width appropriate for *role*."""
    if role in _MARKER_ROLES:
        return _MARKER_GUTTER_WIDTH
    return _STATUS_GUTTER_WIDTH


def is_internal_turn(text: str) -> bool:
    """Whether ``text`` is an internal turn no transcript should ever show.

    ``/choose`` drives an exclusive-stdin picker; the line is machinery, not
    something the user typed. Lives here, in the leaf both the live echo and the
    ``/resume`` replay import, so a restored session cannot surface what the
    live echo deliberately hides — and so neither side imports the other.
    """
    stripped = text.strip()
    return stripped == "/choose" or stripped.startswith("/choose ")


#: Commands that move between sessions. They belong to the live turn that ran
#: them, never to a replayed transcript: a session's file records the navigation
#: *into* it, so replaying those rows shows the command that opened the very
#: conversation you are reading.
_NAVIGATION_COMMANDS = ("/resume", "/sessions")


def is_navigation_turn(text: str) -> bool:
    """Whether ``text`` is session navigation, which no replay should show."""
    stripped = text.strip()
    return any(
        stripped == command or stripped.startswith(f"{command} ")
        for command in _NAVIGATION_COMMANDS
    )


def transcript_prefix(role: TranscriptRole) -> str:
    """Pad a transcript marker to its role's body column."""
    return role.value.ljust(_gutter_width(role))


def compact_transcript_prefix(role: TranscriptRole) -> str:
    """Return a marker with one trailing cell for constrained rows."""
    return f"{role.value} "


def transcript_continuation(role: TranscriptRole) -> str:
    """Return whitespace aligned with the start of a role's body text."""
    return " " * _gutter_width(role)


def transcript_label(role: TranscriptRole, *, style: str) -> Text:
    """Build a styled label cell aligned to the shared transcript gutter."""
    return Text(transcript_prefix(role), style=style)


def transcript_line(role: TranscriptRole, body: str) -> str:
    """Build a plain transcript row for collapsed or non-interactive output."""
    return f"{transcript_prefix(role)}{body}"


class _GutterRow:
    """Lay a renderable beside a fixed-width marker, emitting unpadded rows."""

    def __init__(
        self,
        body: RenderableType,
        *,
        lead_cell: Text,
        gutter_width: int,
        continuation_cell: Text | None = None,
        background: str = "",
    ) -> None:
        self._body = body
        self._lead_cell = lead_cell
        self._gutter_width = gutter_width
        self._continuation_cell = continuation_cell
        self._background = background

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        # ``height=None`` so the body is never padded out to a fixed row count,
        # and ``overflow="fold"`` to match the column the grid used to declare.
        body_options = options.update(
            width=max(1, options.max_width - self._gutter_width),
            height=None,
            overflow="fold",
        )

        fill = console.get_style(self._background, default="none") if self._background else None

        def _cell(text: Text) -> Segment:
            style = console.get_style(text.style or "none", default="none")
            return Segment(text.plain, fill + style if fill else style)

        lead = _cell(self._lead_cell)
        continuation = (
            _cell(self._continuation_cell)
            if self._continuation_cell is not None
            else Segment(" " * self._gutter_width, fill)
        )
        rendered = console.render_lines(self._body, body_options, pad=False, style=fill)
        for index, line in enumerate(rendered):
            row = [lead if index == 0 else continuation, *line]
            if fill is None:
                yield from trim_row_padding(row)
                yield Segment.line()
                continue
            # Pad at paint time, never at write time: the transcript store
            # re-renders each entry at the current width, so the fill is always
            # the right length. Baking it in at submit time left trailing cells
            # sized for whatever width the terminal had then, which the terminal
            # re-wrapped on its own terms (a named cause in the #6425 ghosting).
            pad = max(0, options.max_width - Segment.get_line_length(row))
            yield from row
            if pad:
                yield Segment(" " * pad, fill)
            yield Segment.line()


def transcript_gutter(
    body: RenderableType,
    *,
    lead: bool,
    role: TranscriptRole = TranscriptRole.ASSISTANT,
    label_style: str = "",
    repeat_lead: bool = False,
    background: str = "",
) -> _GutterRow:
    """Lay a renderable in the gutter appropriate for its transcript role.

    With ``repeat_lead`` the marker is drawn on every wrapped row instead of
    the first only, which keeps a multi-row block legible after a reflow.
    """
    gutter_width = _gutter_width(role)
    lead_cell = transcript_label(role, style=label_style) if lead else Text(" " * gutter_width)
    return _GutterRow(
        body,
        lead_cell=lead_cell,
        gutter_width=gutter_width,
        continuation_cell=lead_cell if repeat_lead and lead else None,
        background=background,
    )


def user_turn_renderable(
    text: str, *, marker_style: str, body_style: str, background: str = ""
) -> _GutterRow:
    """Build the transcript row for one submitted user turn.

    ``❱`` is the heavy *bracket* ornament rather than the heavy *quotation
    mark* (``❯``) — the same chevron, drawn taller, so it holds its own beside
    the assistant's ``●`` instead of thinning out in a light terminal font.
    Single-cell width, so the gutter holds where ``▶``/``◆`` (ambiguous width)
    would shift every continuation row. Drawn on the first row only — repeating
    it down a wrapped turn reads as several prompts rather than one.

    ``text`` is rendered verbatim: it is untrusted input and must never be
    parsed as console markup.
    """
    return transcript_gutter(
        Text(text, style=body_style),
        lead=True,
        role=TranscriptRole.USER,
        label_style=marker_style,
        background=background,
    )


__all__ = [
    "TranscriptRole",
    "is_internal_turn",
    "is_navigation_turn",
    "compact_transcript_prefix",
    "transcript_continuation",
    "transcript_gutter",
    "transcript_label",
    "transcript_line",
    "transcript_prefix",
    "user_turn_renderable",
]
