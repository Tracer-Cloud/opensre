"""Record lists that reflow between summary columns and labeled rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich import box
from rich.cells import cell_len
from rich.console import Console, ConsoleOptions, RenderResult
from rich.padding import Padding
from rich.text import Text

from infrastructure.terminal.theme import BOLD_BRAND, DIM
from surfaces.shared.terminal.components.rendering import repl_table

_MAX_PRIMARY_COLUMN_WIDTH = 48
_OUTER_PADDING = 2
_COLUMN_GAP = 4
_DETAIL_INDENT = 2


@dataclass(frozen=True)
class RecordColumn:
    """A content-sized summary column."""

    header: str
    justify: Literal["left", "right"] = "left"


@dataclass(frozen=True)
class RecordRow:
    """Summary cells, supporting details and identifiers rendered as literal Text."""

    cells: tuple[Text, ...]
    details: tuple[Text, ...] = ()
    metadata: tuple[Text, ...] = ()


@dataclass(frozen=True)
class RecordTable:
    """Recompute layout on every render, including transcript replay after resize."""

    title: str
    columns: tuple[RecordColumn, ...]
    rows: tuple[RecordRow, ...]
    subtitle: str = ""
    caption: str = ""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        # Size only summary cells; full identifiers must not widen the name column.
        widths = [cell_len(column.header) for column in self.columns]
        for row in self.rows:
            for index, cell in enumerate(row.cells):
                content_width = max((cell_len(line) for line in cell.plain.splitlines()), default=0)
                widths[index] = max(widths[index], content_width)
        if widths:
            widths[0] = min(widths[0], _MAX_PRIMARY_COLUMN_WIDTH)
        available = max(1, options.max_width - 2 * _OUTER_PADDING)
        wide = sum(widths) + _COLUMN_GAP * (len(widths) - 1) <= available
        yield Padding(
            Text(f"{self.title} · {len(self.rows)}", style=BOLD_BRAND), (0, _OUTER_PADDING)
        )
        if self.subtitle:
            yield Padding(Text(self.subtitle, style=DIM), (0, _OUTER_PADDING))
        yield Text("")
        for index, row in enumerate(self.rows):
            if wide:
                # SIMPLE_HEAD contributes one separator cell in addition to padding.
                table = repl_table(
                    box=box.SIMPLE_HEAD,
                    show_header=index == 0,
                    padding=(0, _COLUMN_GAP - 1),
                    collapse_padding=True,
                )
                for column, width in zip(self.columns, widths, strict=True):
                    table.add_column(
                        column.header, width=width, justify=column.justify, overflow="fold"
                    )
                table.add_row(*row.cells)
                yield Padding(table, (0, _OUTER_PADDING))
            else:
                yield Padding(row.cells[0], (0, _OUTER_PADDING))
            if row.metadata:
                yield Text("")
                for line in row.metadata:
                    yield Padding(line, (0, _OUTER_PADDING))
            if not wide and len(self.columns) > 1:
                yield Text("")
                for column, cell in zip(self.columns[1:], row.cells[1:], strict=True):
                    yield Padding(
                        Text.assemble((f"{column.header}: ", DIM), cell),
                        (0, _OUTER_PADDING, 0, _OUTER_PADDING + _DETAIL_INDENT),
                    )
            # Description helpers may already supply separators. Normalize them here
            # so absent summaries never create leading, trailing or doubled gaps.
            separator = True
            for line in row.details:
                if not line.plain.strip():
                    separator = True
                    continue
                if separator:
                    yield Text("")
                yield Padding(line, (0, _OUTER_PADDING, 0, _OUTER_PADDING + _DETAIL_INDENT))
                separator = False
            if index < len(self.rows) - 1:
                yield Text("")
        if self.caption:
            yield Text("")
            yield Padding(Text(self.caption, style=DIM), (0, _OUTER_PADDING))
