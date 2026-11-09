"""Bounded, read-only work-list presentation for interactive terminals."""

from __future__ import annotations

from collections.abc import Sequence

from rich import box
from rich.console import RenderableType
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text

from core.domain.work_items import WorkItem
from infrastructure.terminal.theme import DIM, HIGHLIGHT, INPUT_SURFACE, TEXT
from surfaces.shared.terminal.tables.work_items import work_items_table


def work_list_panel(items: Sequence[WorkItem], *, title: str) -> Panel:
    """Frame the shared reflowing records without fixing their rendered height."""
    content: RenderableType
    if items:
        content = work_items_table(items, title=title)
    else:
        content = Padding(
            Text.assemble(
                ("no matching work items. Add one with ", DIM),
                ("/work add <title>", HIGHLIGHT),
                (".", DIM),
            ),
            (0, 2),
        )
    return Panel(
        content,
        title=Text("/work list"),
        box=box.SQUARE,
        width=96,
        padding=(1, 0),
        style=f"{TEXT} on {INPUT_SURFACE}",
        border_style=str(HIGHLIGHT),
    )
