"""The live reply preview: model text pinned in the prompt region."""

from __future__ import annotations

import re

from surfaces.interactive_shell.ui.reply_preview import REPLY_PREVIEW_ROWS, reply_preview_ansi
from surfaces.shared.terminal.prompt_layout import prompt_text_width

_SGR = re.compile(r"\x1b\[[0-9;]*m")


def test_preview_shows_the_newest_rows_within_width_without_terminal_controls() -> None:
    text = "first line\n\x1b[2J\x07" + "word " * 60 + "\nlast line"

    rendered = reply_preview_ansi(text, width=40)
    rows = [_SGR.sub("", row) for row in rendered.splitlines()]

    assert len(rows) == REPLY_PREVIEW_ROWS
    assert rows[-1].strip() == "last line"
    # A row wider than the region would soft-wrap and desync its row count.
    assert all(prompt_text_width(row) <= 40 for row in rows)
    assert "\x1b[2J" not in rendered
    assert "\x07" not in rendered


def test_blank_reply_renders_no_rows() -> None:
    assert reply_preview_ansi(" \n\n", width=40) == ""
