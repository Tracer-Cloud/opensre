"""Record lists that reflow between summary columns and labeled rows."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.text import Text

from infrastructure.terminal.theme import BOLD_BRAND, DIM
from surfaces.shared.terminal.components.rendering import repl_table


@dataclass(frozen=True)
class RecordColumn:
    """A summary column; the first column receives the remaining width."""

    header: str
    width: int = 20
    justify: Literal["left", "right"] = "left"


@dataclass(frozen=True)
class RecordRow:
    """Summary cells and full-width secondary lines, already escaped as Text."""

    cells: tuple[Text, ...]
    details: tuple[Text, ...] = ()


@dataclass(frozen=True)
class RecordTable:
    """Recompute layout on every render, including transcript replay after resize."""

    title: str
    columns: tuple[RecordColumn, ...]
    rows: tuple[RecordRow, ...]
    subtitle: str = ""
    caption: str = ""

    def __rich_console__(self, _console: Console, options: ConsoleOptions) -> RenderResult:
        yield Text(f"{self.title} · {len(self.rows)}", style=BOLD_BRAND)
        if self.subtitle:
            yield Text(self.subtitle, style=DIM)
        yield Text("")
        first_width = options.max_width - sum(col.width + 3 for col in self.columns[1:])
        wide = options.max_width >= 72 and first_width >= self.columns[0].width
        for index, row in enumerate(self.rows):
            if wide:
                table = repl_table(box=box.SIMPLE_HEAD, show_header=index == 0)
                for column_index, column in enumerate(self.columns):
                    table.add_column(
                        column.header,
                        width=first_width if column_index == 0 else column.width,
                        justify=column.justify,
                        overflow="fold",
                    )
                table.add_row(*row.cells)
                yield table
            else:
                yield row.cells[0]
                for column, cell in zip(self.columns[1:], row.cells[1:], strict=True):
                    yield Text.assemble((f"{column.header}: ", DIM), cell)
            yield from row.details
            if index < len(self.rows) - 1:
                yield Text("")
        if self.caption:
            yield Text("")
            yield Text(self.caption, style=DIM)
