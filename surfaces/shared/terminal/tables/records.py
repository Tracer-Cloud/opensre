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
_DEFAULT_FRAME_WIDTH = 96
_FRAME_CHROME_WIDTH = 4


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

    @property
    def column_gap(self) -> int:
        """Return the shared gutter between summary columns."""
        return _COLUMN_GAP

    def _summary_widths(self) -> tuple[int, ...]:
        # Size only summary cells; full identifiers must not widen the name column.
        widths = [cell_len(column.header) for column in self.columns]
        for row in self.rows:
            for index, cell in enumerate(row.cells):
                content_width = max((cell_len(line) for line in cell.plain.splitlines()), default=0)
                widths[index] = max(widths[index], content_width)
        if widths:
            widths[0] = min(widths[0], _MAX_PRIMARY_COLUMN_WIDTH)
        return tuple(widths)

    @property
    def summary_content_width(self) -> int:
        """Return the width needed to keep all summary columns on one row."""
        widths = self._summary_widths()
        gaps = _COLUMN_GAP * max(0, len(widths) - 1)
        return sum(widths) + gaps + 2 * _OUTER_PADDING

    def summary_widths(self, available: int) -> tuple[int, ...] | None:
        """Return content-sized columns, or ``None`` when labeled rows are needed."""
        widths = self._summary_widths()
        if sum(widths) + _COLUMN_GAP * max(0, len(widths) - 1) <= available:
            return widths
        return None

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        widths = self.summary_widths(max(1, options.max_width - 2 * _OUTER_PADDING))
        yield Padding(
            Text(f"{self.title} · {len(self.rows)}", style=BOLD_BRAND),
            (0, _OUTER_PADDING),
            expand=False,
        )
        if self.subtitle:
            yield Padding(Text(self.subtitle, style=DIM), (0, _OUTER_PADDING), expand=False)
        yield Text("")
        for index, row in enumerate(self.rows):
            if widths is not None:
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
                yield Padding(table, (0, _OUTER_PADDING), expand=False)
            else:
                yield Padding(row.cells[0], (0, _OUTER_PADDING), expand=False)
            if row.metadata:
                yield Text("")
                for line in row.metadata:
                    yield Padding(line, (0, _OUTER_PADDING), expand=False)
            if widths is None and len(self.columns) > 1:
                yield Text("")
                for column, cell in zip(self.columns[1:], row.cells[1:], strict=True):
                    if not cell.plain.strip():
                        continue
                    yield Padding(
                        Text.assemble((f"{column.header}: ", DIM), cell),
                        (0, _OUTER_PADDING, 0, _OUTER_PADDING + _DETAIL_INDENT),
                        expand=False,
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
                yield Padding(
                    line, (0, _OUTER_PADDING, 0, _OUTER_PADDING + _DETAIL_INDENT), expand=False
                )
                separator = False
            if index < len(self.rows) - 1:
                yield Text("")
        if self.caption:
            yield Text("")
            yield Padding(Text(self.caption, style=DIM), (0, _OUTER_PADDING), expand=False)


def record_frame_width(table: RecordTable) -> int:
    """Keep compact lists bounded while giving dense columns the width they need."""
    return max(_DEFAULT_FRAME_WIDTH, table.summary_content_width + _FRAME_CHROME_WIDTH)
