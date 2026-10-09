"""Responsive presentation of long-term memory summaries."""

from __future__ import annotations

from collections.abc import Sequence

from rich.console import Console
from rich.text import Text

from core.domain.memory import MemoryRecord
from infrastructure.terminal.theme import DIM
from surfaces.shared.terminal.components.rendering import print_repl_renderable
from surfaces.shared.terminal.tables.descriptions import description_details
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


def render_memories(console: Console, records: Sequence[MemoryRecord]) -> None:
    """Show memory metadata and descriptions without exposing the stored bodies."""
    print_repl_renderable(
        console,
        RecordTable(
            "Long-term memory",
            (
                RecordColumn("Name"),
                RecordColumn("Type", 22),
                RecordColumn("Updated", 10),
            ),
            tuple(
                RecordRow(
                    (
                        Text(record.slug, style="bold"),
                        Text(record.memory_type.value, style=DIM),
                        Text(record.updated_at[:10], style=DIM),
                    ),
                    description_details(record.description),
                )
                for record in records
            ),
            caption="Open: /memory show <name>",
        ),
    )
