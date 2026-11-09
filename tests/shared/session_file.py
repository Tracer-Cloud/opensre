"""Shared session-file JSONL integrity assertion.

Several concurrency tests re-check session-file integrity ad hoc, each a weaker
version of the same idea ("the file is non-empty", "the last line parses"). The
weak versions pass on a file that has lost turns or gained a second header. This
is the one strong check they can share — strong enough to catch a lost turn or a
duplicate header, and it names the exact offending line when it fails.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from pathlib import Path

__all__ = ["assert_session_file_integrity"]


def _at(path: Path, line_no: int, line: str, msg: str) -> str:
    """A failure message pinned to the 1-based line number and a short excerpt."""
    return f"{path}:{line_no}: {msg}\n  line: {line[:50]!r}"


def assert_session_file_integrity(
    path: Path,
    *,
    expected_markers: Iterable[str] = (),
    allow_truncated_tail: bool = False,
) -> list[dict]:
    """Assert a session JSONL file is well-formed and return its parsed records.

    Checks, in order:

    * every non-empty line parses as a JSON **object**;
    * there is exactly one ``type == "session"`` record, and it is the first one;
    * every ``type`` named in ``expected_markers`` is present.

    A JSON-invalid **final** line — an incomplete last write, expected after a
    crash mid-write — fails by default. Pass ``allow_truncated_tail=True`` only
    where a torn tail is an expected outcome rather than a bug, so the two cases
    never blur (a deliberate choice, never an accident).

    On failure the message names the file, the 1-based line number, and the first
    50 characters of the offending line.
    """
    lines = path.read_text(encoding="utf-8").split("\n")
    # A trailing newline leaves a final "" element — that is not a torn tail.
    if lines and lines[-1] == "":
        lines.pop()
    last_index = len(lines) - 1

    records: list[dict] = []
    session_line_nos: list[int] = []
    for index, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            if index == last_index and allow_truncated_tail:
                break  # deliberately tolerated incomplete final write
            raise AssertionError(
                _at(path, index + 1, line, f"does not parse as JSON: {exc.msg}")
            ) from exc
        if not isinstance(record, dict):
            raise AssertionError(_at(path, index + 1, line, "JSONL line is not a JSON object"))
        if record.get("type") == "session":
            session_line_nos.append(index + 1)
        records.append(record)

    assert records, f"{path}: session file has no records"
    assert session_line_nos, f"{path}: missing the type=='session' header record"
    assert len(session_line_nos) == 1, (
        f"{path}: expected exactly one type=='session' record, "
        f"found {len(session_line_nos)} at lines {session_line_nos}"
    )
    assert records[0].get("type") == "session", (
        f"{path}: the type=='session' record must be the first line, "
        f"found it at line {session_line_nos[0]}"
    )

    present = {record.get("type") for record in records}
    missing = [marker for marker in expected_markers if marker not in present]
    assert not missing, f"{path}: expected turn markers missing: {missing}"

    return records
