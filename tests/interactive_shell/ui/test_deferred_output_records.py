"""Disk records preserve Rich block boundaries without joining large snapshots."""

from __future__ import annotations

import pytest

from config.constants.terminal_host import DEFERRED_OUTPUT_CHUNK_CHARS
from surfaces.interactive_shell.ui.input_prompt.deferred_output import DeferredOutput


def test_block_boundary_marks_only_the_first_bounded_unicode_fragment() -> None:
    captured = DeferredOutput()
    snapshot = "告警🙂" * DEFERRED_OUTPUT_CHUNK_CHARS + "\n"
    try:
        captured.write("partial")
        captured.write(snapshot, block=True)
        captured.write("after\n")
        records = list(captured)
    finally:
        captured.close()

    assert records[0] == (False, "partial")
    assert records[-1] == (False, "after\n")
    assert records[1][0] is True
    assert all(not starts_block for starts_block, _text in records[2:])
    assert all(len(text) <= DEFERRED_OUTPUT_CHUNK_CHARS for _starts_block, text in records)
    assert "".join(text for _starts_block, text in records[1:-1]) == snapshot


def test_empty_rich_block_preserves_its_boundary() -> None:
    captured = DeferredOutput()
    try:
        captured.write("partial")
        captured.write("", block=True)
        assert list(captured) == [(False, "partial"), (True, "")]
    finally:
        captured.close()


def test_short_writes_preserve_record_metadata_and_surrogate_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = DeferredOutput()
    original_write = captured._file.write

    def short_write(data: bytes) -> int:
        return original_write(data[:2])

    monkeypatch.setattr(captured._file, "write", short_write)
    try:
        captured.write("alert \udcff🙂\n", block=True)
        assert list(captured) == [(True, "alert \udcff🙂\n")]
    finally:
        captured.close()


def test_failed_block_record_keeps_the_accepted_prefix_and_allows_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = DeferredOutput()
    captured.write("accepted\n")
    original_write = captured._file.write
    calls = 0

    def failing_write(data: bytes) -> int:
        nonlocal calls
        calls += 1
        if calls == 2:
            original_write(data[:2])
            raise OSError("disk full")
        return original_write(data)

    monkeypatch.setattr(captured._file, "write", failing_write)
    try:
        with pytest.raises(OSError, match="disk full"):
            captured.write("failed alert\n", block=True)
        monkeypatch.setattr(captured._file, "write", original_write)
        captured.write("recovered alert\n", block=True)
        assert list(captured) == [(False, "accepted\n"), (True, "recovered alert\n")]
    finally:
        captured.close()


def test_appending_while_reading_preserves_the_unread_prefix_and_new_tail() -> None:
    captured = DeferredOutput()
    try:
        captured.write("first\n")
        captured.write("second\n")
        records = iter(captured)
        assert next(records) == (False, "first\n")
        captured.write("appended\n")
        assert list(records) == [(False, "second\n"), (False, "appended\n")]
    finally:
        captured.close()


def test_interleaved_readers_keep_independent_record_offsets() -> None:
    captured = DeferredOutput()
    try:
        captured.write("first\n")
        captured.write("second\n")
        first = iter(captured)
        second = iter(captured)
        assert next(first) == (False, "first\n")
        assert next(second) == (False, "first\n")
        assert next(first) == (False, "second\n")
        assert next(second) == (False, "second\n")
    finally:
        captured.close()
