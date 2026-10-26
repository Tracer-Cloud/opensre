"""Shared work-item presentation for the CLI and interactive shell."""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text

from core.domain.work_items import WorkItem, WorkItemScore
from infrastructure.terminal.theme import DIM, HIGHLIGHT, WARNING
from surfaces.shared.terminal.components.time_format import format_repl_timestamp
from surfaces.shared.terminal.tables.descriptions import description_details
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def work_items_table(items: Sequence[WorkItem], *, title: str = "Work items") -> RecordTable:
    """Prioritize the task title, retaining a complete ID and timezone-aware due time."""
    show_project = any(item.project.strip() for item in items)
    return RecordTable(
        title,
        (
            RecordColumn("Work item"),
            *((RecordColumn("Project"),) if show_project else ()),
            RecordColumn("State"),
            RecordColumn("Priority"),
            RecordColumn("Due"),
        ),
        tuple(
            RecordRow(
                (
                    Text(item.title, style="bold"),
                    *((Text(item.project.strip()),) if show_project else ()),
                    Text(
                        item.status.value.capitalize(),
                        style=WARNING if item.status.value == "blocked" else HIGHLIGHT,
                    ),
                    Text(item.priority.value.capitalize()),
                    Text(format_repl_timestamp(item.due_at, style="utc").removesuffix(" UTC")),
                ),
                metadata=(Text(f"ID: {item.id}", style=DIM),),
            )
            for item in items
        ),
        subtitle="Due times: UTC",
    )


def next_work_table(ranked: Sequence[WorkItemScore]) -> RecordTable:
    """Keep ranking explanations out of constrained summary columns."""
    show_project = any(scored.item.project.strip() for scored in ranked)
    return RecordTable(
        "Recommended next work",
        (
            RecordColumn("Work item"),
            *((RecordColumn("Project"),) if show_project else ()),
            RecordColumn("Rank", "right"),
            RecordColumn("Score", "right"),
        ),
        tuple(
            RecordRow(
                (
                    Text(scored.item.title, style="bold"),
                    *((Text(scored.item.project.strip()),) if show_project else ()),
                    Text(str(index)),
                    Text(str(scored.score)),
                ),
                description_details(f"Why: {', '.join(scored.reasons)}", width=None),
                metadata=(Text(f"ID: {scored.item.id}", style=DIM),),
            )
            for index, scored in enumerate(ranked, start=1)
        ),
    )
