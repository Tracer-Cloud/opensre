"""Markdown renderable for terminal replies.

Rich's stock markdown table has no column rules and cuts long cells with an
ellipsis; a reply table here wraps every cell and draws minimal column rules
under a heavy header rule. Lives beside the theme so every tier that paints a
reply (surfaces, integrations) renders markdown the same way.

Rich prints only a link's text and hides the URL in an OSC 8 escape, which
terminals without OSC 8 (macOS Terminal.app) cannot open and copying drops. A
reply link keeps that escape, is painted with ``markdown.link``, and is
followed by `` (url)`` unless its text is exactly the URL.

Rows bound for scrollback must also carry no trailing padding. The terminal
owns them once written and reflows them on a width change, and padding that
is invisible at the width it was written for wraps onto a second, blank row
after a shrink. Rich pads because ``Markdown`` forces ``justify="left"``,
which left-justifies by truncating with ``pad=True``; :class:`UnpaddedRows`
undoes that for any renderable.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from markdown_it.token import Token
from rich import box
from rich.console import Console, ConsoleOptions, RenderResult
from rich.markdown import Markdown, MarkdownElement, TableElement
from rich.segment import Segment
from rich.table import Table

import infrastructure.terminal.theme as ui_theme

if TYPE_CHECKING:
    from collections.abc import Iterable

    from rich.console import RenderableType

#: Minimal borders shared by reply tables and the shell's own tables.
REPLY_TABLE_BOX = box.MINIMAL_HEAVY_HEAD

#: Inline tag Rich resolves to the ``markdown.link`` style; wraps a link's text.
_LINK_TEXT_TAG = "link"


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


def _url_after_text(href: str) -> tuple[Token, ...]:
    """Inline tokens for `` (href)``; the URL itself stays a hyperlink."""
    return (
        Token("text", "", 0, content=" ("),
        Token("link_open", "a", 1, attrs={"href": href}),
        Token("text", "", 0, content=href),
        Token("link_close", "a", -1),
        Token("text", "", 0, content=")"),
    )


def _show_link_urls(children: list[Token]) -> list[Token]:
    """Paint each link's text with ``markdown.link`` and print its URL after it.

    Rich's hyperlink branch paints link text with ``markdown.link_url``; a
    ``_LINK_TEXT_TAG`` pair inside the link layers ``markdown.link`` over it.
    The URL is skipped only when the text is exactly the URL, so it never
    prints twice; a decoded form such as ``a/b`` for ``a%2Fb`` may reach a
    different resource and still gets the real URL.
    """
    shown: list[Token] = []
    link: Token | None = None
    label: list[str] = []
    for token in children:
        if token.type == "link_open":
            link, label = token, []
            shown += (token, Token("link_text_open", _LINK_TEXT_TAG, 1))
        elif token.type == "link_close" and link is not None:
            shown += (Token("link_text_close", _LINK_TEXT_TAG, -1), token)
            href = str(link.attrs.get("href", ""))
            # markdown-it marks autolinks ``auto`` and builds their text from the
            # URL, decoding only escapes that keep the destination (not ``%2F``).
            if link.info != "auto" and "".join(label).strip() != href:
                shown += _url_after_text(href)
            link = None
        else:
            if link is not None and token.type in {"text", "code_inline"}:
                label.append(token.content)
            shown.append(token)
    return shown


class ReplyMarkdown(Markdown):
    """``rich.Markdown`` in the reply theme, with reply tables and links that show their URL."""

    elements: ClassVar[dict[str, type[MarkdownElement]]] = {
        **Markdown.elements,
        "table_open": ReplyTableElement,
    }
    inlines = Markdown.inlines | {_LINK_TEXT_TAG}

    def __init__(self, markup: str, code_theme: str = "monokai") -> None:
        # No ``hyperlinks`` switch: the link rewrite relies on Rich's hyperlink branch.
        super().__init__(markup, code_theme=code_theme)
        for token in self.parsed:
            if token.children:
                token.children = _show_link_urls(token.children)

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        # Apply the active reply theme even on a console the caller never
        # themed, such as the REPL's buffer console. Render eagerly so the
        # theme is popped before anything else on the console renders.
        with console.use_theme(ui_theme.MARKDOWN_THEME):
            segments = list(super().__rich_console__(console, options))
        yield from segments


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
