"""Prompt-toolkit record rows using the shared record table's column geometry."""

from __future__ import annotations

from functools import lru_cache

from prompt_toolkit.formatted_text import StyleAndTextTuples
from prompt_toolkit.utils import get_cwidth
from rich.text import Text

from infrastructure.safety.terminal_output import strip_terminal_controls
from surfaces.shared.terminal.prompt_layout import clip_prompt_text
from surfaces.shared.terminal.tables.records import RecordColumn, RecordRow, RecordTable


class RecordChoices:
    """Render literal, single-style cells with stable columns across search results."""

    def __init__(self, columns: tuple[RecordColumn, ...], records: dict[str, RecordRow]) -> None:
        self.records = {
            key: RecordRow(
                tuple(
                    Text(" ".join(strip_terminal_controls(cell.plain).split()), style=cell.style)
                    for cell in record.cells
                )
            )
            for key, record in records.items()
        }
        self.table = RecordTable("", columns, tuple(self.records.values()))
        self.widths = lru_cache(maxsize=8)(self.table.summary_widths)

    def header(self, width: int) -> StyleAndTextTuples:
        """Align headings with wide rows; stacked rows carry their own labels."""
        widths = self.widths(max(1, width - 2))
        if widths is None:
            return []
        cells = tuple(
            Text(column.header, style="class:description") for column in self.table.columns
        )
        return [("", "  "), *self._columns(cells, widths)]

    def row_height(self, width: int) -> int:
        """Count bounded preview lines, excluding the gap between records."""
        if self.widths(max(1, width - 2)) is not None:
            return 1
        return sum(column.header != "ID" for column in self.table.columns)

    def row(self, key: str, width: int, *, selected: bool) -> StyleAndTextTuples:
        """Paint the complete selected row without lowering cell contrast."""
        record = self.records[key]
        widths = self.widths(max(1, width - 2))
        if widths is not None:
            lines = [self._columns(record.cells, widths)]
        else:
            lines = [
                [(str(record.cells[0].style), clip_prompt_text(record.cells[0].plain, width - 2))]
            ]
            for column, cell in zip(self.table.columns[1:], record.cells[1:], strict=True):
                if column.header == "ID":
                    continue  # The pinned detail area keeps the complete selected ID readable.
                lines.append(
                    [
                        (
                            str(cell.style),
                            clip_prompt_text(f"{column.header}: {cell.plain or 'None'}", width - 2),
                        )
                    ]
                )
        result: StyleAndTextTuples = []
        background = " class:record-selected" if selected else ""
        for index, line in enumerate(lines):
            marker = "› " if selected and index == 0 else "  "
            result.append(("class:record-marker" + background, marker))
            result.extend((style + background, text) for style, text, *_rest in line)
            used = 2 + sum(get_cwidth(fragment[1]) for fragment in line)
            result.append((background, " " * max(0, width - used)))
            result.append(("", "\n"))
        return result

    def _columns(self, cells: tuple[Text, ...], widths: tuple[int, ...]) -> StyleAndTextTuples:
        result: StyleAndTextTuples = []
        for index, (column, cell, width) in enumerate(
            zip(self.table.columns, cells, widths, strict=True)
        ):
            if index:
                result.append(("", " " * self.table.column_gap))
            value = clip_prompt_text(cell.plain, width)
            padding = " " * max(0, width - get_cwidth(value))
            result.append(
                (str(cell.style), padding + value if column.justify == "right" else value + padding)
            )
        return result
