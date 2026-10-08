"""Shared work-item presentation for the CLI and interactive shell."""

from __future__ import annotations

from collections.abc import Sequence

from rich.text import Text

from core.domain.work_items import WorkItem, WorkItemScore
from infrastructure.terminal.theme import DIM, HIGHLIGHT, WARNING
from surfaces.shared.terminal.components.time_format import format_repl_timestamp
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def _details(item: WorkItem) -> tuple[Text, ...]:
    return (
        Text(f"ID: {item.id}", style=DIM),
        Text(f"Project: {item.project or '—'}", style=DIM),
    )


def work_items_table(items: Sequence[WorkItem], *, title: str = "Work items") -> RecordTable:
    """Prioritize the task title, retaining a complete ID and timezone-aware due time."""
    return RecordTable(
        title,
        (
            RecordColumn("Work item"),
            RecordColumn("State", 10),
            RecordColumn("Priority", 8),
            RecordColumn("Due", 19),
        ),
        tuple(
            RecordRow(
                (
                    Text(item.title, style="bold"),
                    Text(
                        item.status.value.capitalize(),
                        style=WARNING if item.status.value == "blocked" else HIGHLIGHT,
                    ),
                    Text(item.priority.value.capitalize()),
                    Text(format_repl_timestamp(item.due_at, style="utc").removesuffix(" UTC")),
                ),
                _details(item),
            )
            for item in items
        ),
        subtitle="Due times: UTC",
    )


def next_work_table(ranked: Sequence[WorkItemScore]) -> RecordTable:
    """Keep ranking explanations out of constrained summary columns."""
    return RecordTable(
        "Recommended next work",
        (
            RecordColumn("Work item"),
            RecordColumn("Rank", 4, "right"),
            RecordColumn("Score", 6, "right"),
        ),
        tuple(
            RecordRow(
                (
                    Text(scored.item.title, style="bold"),
                    Text(str(index)),
                    Text(str(scored.score)),
                ),
                (*_details(scored.item), Text(f"Why: {', '.join(scored.reasons)}", style=DIM)),
            )
            for index, scored in enumerate(ranked, start=1)
        ),
    )
