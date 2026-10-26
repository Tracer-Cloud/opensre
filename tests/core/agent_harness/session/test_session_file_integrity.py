"""The shared session-file integrity assertion: the failure modes it must catch."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.shared.session_file import assert_session_file_integrity


def _write(path: Path, *records: dict) -> Path:
    path.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    return path


def test_well_formed_file_passes_and_returns_records(tmp_path: Path) -> None:
    path = _write(
        tmp_path / "s.jsonl",
        {"type": "session", "id": "s1"},
        {"type": "message", "content": "hi"},
        {"type": "leaf"},
    )
    rows = assert_session_file_integrity(path, expected_markers={"message", "leaf"})
    assert [r["type"] for r in rows] == ["session", "message", "leaf"]


def test_blank_lines_are_ignored(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text('{"type": "session"}\n\n{"type": "leaf"}\n\n', encoding="utf-8")
    assert len(assert_session_file_integrity(path)) == 2


def test_a_second_session_header_fails(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.jsonl", {"type": "session"}, {"type": "leaf"}, {"type": "session"})
    with pytest.raises(AssertionError, match="exactly one type=='session'"):
        assert_session_file_integrity(path)


def test_missing_header_fails(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.jsonl", {"type": "message"}, {"type": "leaf"})
    with pytest.raises(AssertionError, match="missing the type=='session' header"):
        assert_session_file_integrity(path)


def test_header_not_first_fails(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.jsonl", {"type": "message"}, {"type": "session"})
    with pytest.raises(AssertionError, match="must be the first line"):
        assert_session_file_integrity(path)


def test_unparseable_line_reports_line_number_and_excerpt(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text('{"type": "session"}\nNOT JSON HERE\n', encoding="utf-8")
    # (?s) so .* crosses the newline between the reason and the excerpt line.
    with pytest.raises(AssertionError, match=r"(?s)s\.jsonl:2:.*NOT JSON HERE"):
        assert_session_file_integrity(path)


def test_truncated_final_line_fails_by_default_but_can_be_allowed(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    path.write_text(
        '{"type": "session"}\n{"type": "leaf", "content":', encoding="utf-8"
    )  # torn tail
    with pytest.raises(AssertionError):
        assert_session_file_integrity(path)
    # Deliberately tolerated: the header still stands and the good record parses.
    rows = assert_session_file_integrity(path, allow_truncated_tail=True)
    assert [r["type"] for r in rows] == ["session"]


def test_missing_expected_marker_fails(tmp_path: Path) -> None:
    path = _write(tmp_path / "s.jsonl", {"type": "session"}, {"type": "message"})
    with pytest.raises(AssertionError, match="expected turn markers missing"):
        assert_session_file_integrity(path, expected_markers={"leaf"})
