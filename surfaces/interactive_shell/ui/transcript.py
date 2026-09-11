"""Aligned semantic labels for interactive-shell transcript rows."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:
    from rich.console import RenderableType


class TranscriptRole(StrEnum):
    """Visible roles used to distinguish transcript rows without icon-only state."""

    ASSISTANT = "OpenSRE"
    WORKING = "Working"
    TOOL = "Tool"
    SKILL_LOADED = "Loaded"
    ERROR = "Error"


# Seven characters fits the longest label; two trailing cells separate the
# label from its body without relying on a tiny punctuation glyph.
_TRANSCRIPT_GUTTER_WIDTH = 9


def transcript_prefix(role: TranscriptRole) -> str:
    """Pad a semantic label to the shared transcript body column."""
    return role.value.ljust(_TRANSCRIPT_GUTTER_WIDTH)


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
    """Lay a renderable in the fixed-width semantic transcript gutter."""
    grid = Table.grid(padding=0)
    grid.add_column(width=_TRANSCRIPT_GUTTER_WIDTH, no_wrap=True)
    grid.add_column(overflow="fold")
    lead_cell = (
        transcript_label(role, style=label_style) if lead else Text(" " * _TRANSCRIPT_GUTTER_WIDTH)
    )
    grid.add_row(lead_cell, body)
    return grid


__all__ = [
    "TranscriptRole",
    "transcript_gutter",
    "transcript_label",
    "transcript_line",
    "transcript_prefix",
]
