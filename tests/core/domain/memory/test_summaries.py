"""Session summaries: appended per pass, bounded per session and across sessions."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from config.constants import OPENSRE_MEMORY_DIR_ENV
from core.domain.memory import append_session_summary, memory_dir
from core.domain.memory.files import SESSIONS_DIRNAME
from core.domain.memory.summaries import (
    MAX_ENTRIES_PER_SESSION_FILE,
    MAX_SESSION_SUMMARY_CHARS,
    MAX_SESSION_SUMMARY_FILES,
    recent_session_summaries,
)

T0 = datetime(2026, 10, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def memory_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv(OPENSRE_MEMORY_DIR_ENV, str(tmp_path / "memory"))
    return tmp_path / "memory"


def test_each_pass_appends_and_the_newest_entry_is_read_back() -> None:
    for minute in range(MAX_ENTRIES_PER_SESSION_FILE + 3):
        assert append_session_summary(
            "s-1", f"pass {minute}", outcome="partial", now=T0 + timedelta(minutes=minute)
        )

    text = (memory_dir() / SESSIONS_DIRNAME / "s-1.md").read_text(encoding="utf-8")
    assert text.count("\n## ") == MAX_ENTRIES_PER_SESSION_FILE
    assert "pass 2\n" not in text
    [latest] = recent_session_summaries()
    assert (latest.session_id, latest.outcome, latest.text) == ("s-1", "partial", "pass 12")


def test_summaries_are_capped_and_unknown_outcomes_become_uncertain() -> None:
    append_session_summary("s-1", "x" * 5_000, outcome="victory", now=T0)

    [summary] = recent_session_summaries()
    assert summary.outcome == "uncertain"
    assert len(summary.text) <= MAX_SESSION_SUMMARY_CHARS


def test_only_the_newest_session_files_are_kept() -> None:
    sessions = memory_dir() / SESSIONS_DIRNAME
    for index in range(MAX_SESSION_SUMMARY_FILES + 4):
        append_session_summary(f"s-{index}", f"session {index}", now=T0)
        # Distinct mtimes without sleeping: oldest index, oldest file.
        stamp = (T0 + timedelta(minutes=index)).timestamp()
        os.utime(sessions / f"s-{index}.md", (stamp, stamp))
    append_session_summary("s-new", "trigger pruning", now=T0)

    names = {path.stem for path in sessions.glob("*.md")}
    assert len(names) == MAX_SESSION_SUMMARY_FILES
    assert "s-new" in names
    assert {"s-0", "s-1", "s-2", "s-3", "s-4"}.isdisjoint(names)


@pytest.mark.parametrize("session_id", ["../escape", ".hidden", "", "a/b"])
def test_session_ids_that_are_not_file_names_are_refused(session_id: str) -> None:
    assert not append_session_summary(session_id, "summary", now=T0)
    assert recent_session_summaries() == []
