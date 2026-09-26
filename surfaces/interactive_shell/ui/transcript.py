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

    ASSISTANT = "●"
    WORKING = "Working"
    TOOL = "Tool"
    ERROR = "Error"


# Status rows share a body column, while replies keep the compact marker gutter.
_STATUS_GUTTER_WIDTH = 9
_ASSISTANT_GUTTER_WIDTH = 2


def _gutter_width(role: TranscriptRole) -> int:
    """Return the gutter width appropriate for *role*."""
    if role is TranscriptRole.ASSISTANT:
        return _ASSISTANT_GUTTER_WIDTH
    return _STATUS_GUTTER_WIDTH


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

    def __init__(self, body: RenderableType, *, lead_cell: Text, gutter_width: int) -> None:
        self._body = body
        self._lead_cell = lead_cell
        self._gutter_width = gutter_width

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        # ``height=None`` so the body is never padded out to a fixed row count,
        # and ``overflow="fold"`` to match the column the grid used to declare.
        body_options = options.update(
            width=max(1, options.max_width - self._gutter_width),
            height=None,
            overflow="fold",
        )
        lead = Segment(
            self._lead_cell.plain,
            console.get_style(self._lead_cell.style or "none", default="none"),
        )
        continuation = Segment(" " * self._gutter_width)
        for index, line in enumerate(console.render_lines(self._body, body_options, pad=False)):
            yield from trim_row_padding([lead if index == 0 else continuation, *line])
            yield Segment.line()


def transcript_gutter(
    body: RenderableType,
    *,
    lead: bool,
    role: TranscriptRole = TranscriptRole.ASSISTANT,
    label_style: str = "",
) -> _GutterRow:
    """Lay a renderable in the gutter appropriate for its transcript role."""
    gutter_width = _gutter_width(role)
    lead_cell = transcript_label(role, style=label_style) if lead else Text(" " * gutter_width)
    return _GutterRow(body, lead_cell=lead_cell, gutter_width=gutter_width)


__all__ = [
    "TranscriptRole",
    "compact_transcript_prefix",
    "transcript_continuation",
    "transcript_gutter",
    "transcript_label",
    "transcript_line",
    "transcript_prefix",
]
