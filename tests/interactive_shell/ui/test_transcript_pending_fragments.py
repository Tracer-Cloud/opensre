"""Pending output stays cheap to append and preserves transcript boundaries."""

from __future__ import annotations

import tracemalloc
from typing import cast

import pytest
from rich.text import Text

from surfaces.interactive_shell.ui.transcript_view import TranscriptStore


def _plain_rows(store: TranscriptStore) -> list[str]:
    rows, _older = store.tail_rows(80, 20)
    return ["".join(text for _style, text in row).rstrip() for row in rows]


def test_long_unfinished_line_does_not_copy_the_accumulated_output_on_each_append() -> None:
    store = TranscriptStore()
    chunk = "x" * 8192
    count = 512
    payload_size = len(chunk) * count

    tracemalloc.start()
    try:
        for _ in range(count):
            store.append_text(chunk)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    # Keep the payload, but do not allocate another growing copy while appending.
    assert peak < payload_size * 1.5
    [entry] = store.take_unflushed()
    assert cast(Text, entry.renderable).plain == chunk * count


def test_fragmented_lines_keep_ansi_styles_and_carriage_return_overwrites(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NO_COLOR", raising=False)
    store = TranscriptStore()
    for part in ("\x1b[31m", "alert", "!\x1b[0m\n", "working ", "10%\r", "done\n"):
        store.append_text(part)

    rows, _older = store.tail_rows(80, 20)
    assert _plain_rows(store) == ["alert!", "done"]
    assert "".join(text for style, text in rows[0] if "red" in style) == "alert!"


def test_pending_snapshots_survive_later_appends_and_clear() -> None:
    store = TranscriptStore()
    store.append_text("before")
    original = store.window(80, 3, 0, None)
    store.append_text(" after")
    updated = store.window(80, 3, 0, original.mark)

    assert original.mark.pending == "before"
    assert updated.mark.pending == "before after"
    store.append_text("\x1b[2Jfresh")
    assert _plain_rows(store) == ["fresh"]
    assert store.window(80, 3, 1, updated.mark).offset == 0


def test_closing_pending_line_separates_blocks_without_empty_rows_or_notifications() -> None:
    store = TranscriptStore()
    changed: list[None] = []

    def on_change() -> None:
        changed.append(None)

    store.on_change = on_change
    store.close_pending_line()
    assert changed == []
    store.append_text("partial")
    store.close_pending_line()
    store.close_pending_line()
    assert len(changed) == 2
    store.append_text("alert\n")

    assert _plain_rows(store) == ["partial", "alert"]


def test_fragmented_line_started_off_screen_is_still_flushed_to_scrollback() -> None:
    store = TranscriptStore()
    store.append_text("not yet ")
    store.append_text("in scrollback", on_normal_screen=True)
    store.append_text("\n", on_normal_screen=True)

    [entry] = store.take_unflushed()
    assert cast(Text, entry.renderable).plain == "not yet in scrollback"
    assert store.take_unflushed() == []
