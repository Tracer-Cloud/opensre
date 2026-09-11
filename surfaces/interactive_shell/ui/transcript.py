"""Aligned semantic labels for interactive-shell transcript rows."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import RenderableType


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


def transcript_gutter(
    body: RenderableType,
    *,
    lead: bool,
    role: TranscriptRole = TranscriptRole.ASSISTANT,
    label_style: str = "",
) -> Table:
    """Lay a renderable in the gutter appropriate for its transcript role."""
    gutter_width = _gutter_width(role)
    grid = Table.grid(padding=0)
    grid.add_column(width=gutter_width, no_wrap=True)
    grid.add_column(overflow="fold")
    lead_cell = transcript_label(role, style=label_style) if lead else Text(" " * gutter_width)
    grid.add_row(lead_cell, body)
    return grid


__all__ = [
    "TranscriptRole",
    "compact_transcript_prefix",
    "transcript_continuation",
    "transcript_gutter",
    "transcript_label",
    "transcript_line",
    "transcript_prefix",
]
