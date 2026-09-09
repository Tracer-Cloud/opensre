"""Markdown renderable for terminal replies.

Rich's stock markdown table has no column rules and cuts long cells with an
ellipsis; a reply table here wraps every cell and draws minimal column rules
under a heavy header rule. Lives beside the theme so every tier that paints a
reply (surfaces, integrations) renders markdown the same way.
"""

from __future__ import annotations

from typing import ClassVar

from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown, MarkdownElement, TableElement
from rich.table import Table

#: Minimal borders shared by reply tables and the shell's own tables.
REPLY_TABLE_BOX = box.MINIMAL_HEAVY_HEAD


class ReplyTableElement(TableElement):
    """Markdown table with column rules and folded cells."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        table = Table(
            box=REPLY_TABLE_BOX,
            show_edge=False,
            pad_edge=False,
            title_justify="left",
            style="markdown.table.border",
        )
        if self.header is not None and self.header.row is not None:
            for column in self.header.row.cells:
                heading = column.content.copy()
                heading.stylize("markdown.table.header")
                table.add_column(heading, overflow="fold")
        if self.body is not None:
            for row in self.body.rows:
                table.add_row(*[cell.content for cell in row.cells])
        yield table


class ReplyMarkdown(Markdown):
    """``rich.Markdown`` whose tables render through ``ReplyTableElement``."""

    elements: ClassVar[dict[str, type[MarkdownElement]]] = {
        **Markdown.elements,
        "table_open": ReplyTableElement,
    }


__all__ = ["REPLY_TABLE_BOX", "ReplyMarkdown", "ReplyTableElement"]
