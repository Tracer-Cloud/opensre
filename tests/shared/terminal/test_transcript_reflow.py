"""Transcript rows reach the terminal unpadded, so a width shrink reflows cleanly.

A reply is scrollback: the terminal owns it once written, and a resize reflows
it with no chance to re-render. Rows padded out to the render width still fit
one physical row at that width, but after a shrink the run of padding spaces
wraps onto a second, blank row and every reply reads as double spaced.
"""

from __future__ import annotations

import io
import re

import pytest
from rich.console import Console, RenderableType
from rich.markdown import Markdown

import infrastructure.terminal.theme as ui_theme
from infrastructure.terminal.markdown import ReplyMarkdown, UnpaddedRows
from surfaces.interactive_shell.ui.streaming.renderer import (
    render_markdown_block,
    render_note_block,
    render_reply_block,
)
from surfaces.interactive_shell.ui.transcript import TranscriptRole, transcript_gutter

_ANSI = re.compile(r"\x1b\[[0-9;]*m")

_REPLY = """We're at **~64.3 stars/day** over the last 30 days (1,928 new stars), but\
 momentum is clearly cooling — the rate dropped to ~50.5/day over 14 days.

Here's the shape of it:

| Window  | Stars | Rate     |
|---------|-------|----------|
| 30 days | 1,928 | 64.3/day |

Net read: the bump has worn off.
"""

_WIDE = 190
_NARROW = 119


def _rows(render: object, width: int = _WIDE) -> list[str]:
    """Render through a console of *width* and return the plain emitted rows."""
    buf = io.StringIO()
    console = Console(file=buf, width=width, force_terminal=False, highlight=False)
    with console.use_theme(ui_theme.MARKDOWN_THEME):
        render(console)  # type: ignore[operator]
    return [_ANSI.sub("", line) for line in buf.getvalue().split("\n")]


def _reflow(rows: list[str], width: int) -> list[str]:
    """How the terminal lays *rows* out after the window shrinks to *width*."""
    physical: list[str] = []
    for row in rows:
        if not row:
            physical.append("")
            continue
        physical.extend(row[start : start + width] for start in range(0, len(row), width))
    return physical


@pytest.mark.parametrize(
    ("name", "render"),
    [
        ("render_reply_block", lambda c: render_reply_block(c, _REPLY)),
        ("render_note_block", lambda c: render_note_block(c, _REPLY)),
        ("render_markdown_block", lambda c: render_markdown_block(c, _REPLY)),
        (
            "transcript_gutter",
            lambda c: c.print(transcript_gutter(ReplyMarkdown(_REPLY), lead=False)),
        ),
        (
            "transcript_gutter_status_role",
            lambda c: c.print(
                transcript_gutter(ReplyMarkdown(_REPLY), lead=True, role=TranscriptRole.WORKING)
            ),
        ),
        ("unpadded_rows", lambda c: c.print(UnpaddedRows(ReplyMarkdown(_REPLY)))),
    ],
)
def test_scrollback_writers_emit_no_trailing_padding(name: str, render: object) -> None:
    """Every transcript writer must leave rows the terminal can reflow."""
    del name

    padded = [row for row in _rows(render) if row != row.rstrip()]

    assert padded == []


def test_unpadded_rows_is_what_keeps_padding_out() -> None:
    """Guard the premise: stock Rich markdown pads, the wrapper is what removes it."""
    # Arrange / Act: the same markdown with and without the wrapper.
    stock = _rows(lambda c: c.print(Markdown(_REPLY)))
    wrapped = _rows(lambda c: c.print(UnpaddedRows(Markdown(_REPLY))))

    # Assert: stock Rich pads rows out to the render width; the wrapper does not.
    assert [row for row in stock if row != row.rstrip()] != []
    assert [row for row in wrapped if row != row.rstrip()] == []


def test_shrinking_the_window_does_not_double_space_a_reply() -> None:
    """The reported symptom: padded rows wrap into a blank row apiece on shrink."""
    # Arrange: a reply rendered wide, as it would sit in scrollback before a resize.
    rows = _rows(lambda c: render_reply_block(c, _REPLY))
    blank_before = sum(1 for row in rows if not row.strip())

    # Act: the user drags the window narrower and the terminal reflows scrollback.
    reflowed = _reflow(rows, _NARROW)

    # Assert: no row gained a blank partner, so the spacing survives the resize.
    assert sum(1 for row in reflowed if not row.strip()) == blank_before


def test_a_code_block_keeps_its_background_bar() -> None:
    """Trailing spaces that paint a background are the block's bar, not padding."""
    # Arrange: a fenced block, whose background is meant to span the block width.
    body: RenderableType = ReplyMarkdown('```python\nprint("hi")\n```\n')

    # Act
    rows = _rows(lambda c: c.print(UnpaddedRows(body)))

    # Assert: the bar survives — some row still carries its full-width padding.
    assert [row for row in rows if row != row.rstrip()] != []
