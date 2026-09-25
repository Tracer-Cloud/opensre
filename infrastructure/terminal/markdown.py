"""Markdown renderable for terminal replies.

Rich's stock markdown table has no column rules and cuts long cells with an
ellipsis; a reply table here wraps every cell and draws minimal column rules
under a heavy header rule. Lives beside the theme so every tier that paints a
reply (surfaces, integrations) renders markdown the same way.

Reply rows must also reach the terminal with no trailing padding. A reply is
scrollback: once written it belongs to the terminal, and a width change
reflows it with no chance to re-render. Padding is invisible at the width it
was written for, but after a shrink the row no longer fits on one physical
row, so the terminal wraps the run of padding spaces onto a second, blank
row — every reply then reads as double spaced. Rich pads because ``Markdown``
forces ``justify="left"`` on each paragraph and ``Lines.justify`` left-justifies
by truncating with ``pad=True``. :class:`UnpaddedRows` undoes that for any
renderable bound for scrollback.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown, MarkdownElement, TableElement
from rich.segment import Segment
from rich.table import Table

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rich.console import RenderableType

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


def trim_row_padding(row: Iterable[Segment]) -> list[Segment]:
    """Drop a rendered row's trailing padding spaces.

    Stops at a control segment, or at one whose style paints a background —
    that run of spaces is a visible bar (a code block), not padding.
    """
    trimmed = list(row)
    while trimmed:
        last = trimmed[-1]
        if last.control or (last.style is not None and last.style.bgcolor is not None):
            break
        text = last.text.rstrip(" ")
        if text:
            trimmed[-1] = Segment(text, last.style, last.control)
            break
        trimmed.pop()
    return trimmed


class UnpaddedRows:
    """Emit a renderable's rows with their trailing padding removed.

    Wrap anything written to scrollback that Rich would otherwise pad out to
    the render width, so a later width change reflows only real content.
    """

    def __init__(self, body: RenderableType) -> None:
        self._body = body

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        # ``height=None`` so the body is never padded out to a fixed row count.
        for line in console.render_lines(self._body, options.update(height=None), pad=False):
            yield from trim_row_padding(line)
            yield Segment.line()


__all__ = [
    "REPLY_TABLE_BOX",
    "ReplyMarkdown",
    "ReplyTableElement",
    "UnpaddedRows",
    "trim_row_padding",
]
