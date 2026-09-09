"""Markdown renderable for terminal replies.

Rich's stock markdown table has no column rules and cuts long cells with an
ellipsis; a reply table here wraps every cell and draws the same minimal
column rules as ``repl_table``.
"""

from __future__ import annotations

from typing import ClassVar

from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown, MarkdownElement, TableElement

from surfaces.shared.terminal.components.rendering import repl_table


class ReplyTableElement(TableElement):
    """Markdown table with column rules and folded cells."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        table = repl_table(style="markdown.table.border")
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


__all__ = ["ReplyMarkdown", "ReplyTableElement"]
