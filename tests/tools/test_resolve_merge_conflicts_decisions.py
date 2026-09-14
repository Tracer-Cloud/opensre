"""Tests for the per-file decisions: table first, menu, mechanical sides, agent only to combine."""

from __future__ import annotations

import io
import subprocess
from pathlib import Path
from unittest.mock import patch

from rich.console import Console

from integrations.coding_agent import CodingResult
from integrations.git import head_sha, merge_in_progress
from tools.cross_vendor.resolve_merge_conflicts.runner import (
    FileChoice,
    normalize_decision,
    resolve_merge,
)

_VERIFY = "tools.cross_vendor.resolve_merge_conflicts.runner.verify_coding_agent"
_RUN = "tools.cross_vendor.resolve_merge_conflicts.runner.run_coding_task"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


def _stopped_merge_two_files(tmp_path: Path) -> Path:
    """``feature`` and ``main`` conflict in ``a.txt`` and ``b.txt``."""
    work = tmp_path / "work"
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "a.txt").write_text("a base\n")
    (work / "b.txt").write_text("b base\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "checkout", "-b", "feature")
    (work / "a.txt").write_text("a feature\n")
    (work / "b.txt").write_text("b feature\n")
    _git(work, "commit", "-am", "feature")
    _git(work, "checkout", "main")
    (work / "a.txt").write_text("a main\n")
    (work / "b.txt").write_text("b main\n")
    _git(work, "commit", "-am", "main")
    _git(work, "checkout", "feature")
    subprocess.run(["git", "merge", "main"], cwd=work, capture_output=True, text=True)
    assert merge_in_progress(str(work))
    return work


def _never_run(*_args: object, **_kwargs: object) -> CodingResult:
    raise AssertionError("the coding agent must not run for mechanical choices")


def test_sides_are_taken_by_git_and_the_table_is_shown_before_anything_runs(
    tmp_path: Path,
) -> None:
    # Arrange
    work = _stopped_merge_two_files(tmp_path)
    buffer = io.StringIO()
    console = Console(file=buffer, width=120, force_terminal=False, color_system=None)

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=_never_run):
        out = resolve_merge(
            str(work),
            ref=None,
            model=None,
            instructions=None,
            decisions={"a.txt": "Keep ours (feature): a feature", "b.txt": "theirs"},
            console=console,
            approve=None,
        )

    # Assert
    assert out["success"] is True and out["commit_sha"] == head_sha(str(work))
    assert (work / "a.txt").read_text() == "a feature\n"
    assert (work / "b.txt").read_text() == "b main\n"
    assert out["resolutions"] == ["a.txt: kept the feature version", "b.txt: took the main version"]
    assert out["coding_agent_summary"] == "a.txt: kept ours; b.txt: took theirs"
    text = buffer.getvalue()
    assert text.index("(to decide)") < text.index("merged result", text.index("(to decide)"))


def test_undecided_files_open_the_menu_and_the_merge_waits(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge_two_files(tmp_path)
    before = head_sha(str(work))
    asked: list[list[FileChoice]] = []

    def ask(choices: list[FileChoice]) -> bool:
        asked.append(choices)
        return True

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=_never_run):
        out = resolve_merge(
            str(work), ref=None, model=None, instructions=None, decisions={"a.txt": "ours"}, ask=ask
        )

    # Assert: only b.txt is asked, with both sides in the options, and nothing was changed.
    assert [c.path for c in asked[0]] == ["b.txt"]
    assert asked[0][0].options == (
        "Keep ours (feature): b feature",
        "Take theirs (main): b main",
        "Combine both with the coding agent",
    )
    assert out["error_kind"] == "awaiting_decisions"
    assert out["menu"] == "queued"
    assert "call resolve_merge_conflicts again" in out["instruction"]
    assert merge_in_progress(str(work)) and head_sha(str(work)) == before


def test_without_a_menu_undecided_files_go_to_the_coding_agent_only(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge_two_files(tmp_path)
    tasks: list[str] = []

    def combine(task: str, **_kwargs: object) -> CodingResult:
        tasks.append(task)
        (work / "b.txt").write_text("b feature and main\n")
        return CodingResult(success=True, summary="Combined b.txt.")

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=combine):
        out = resolve_merge(
            str(work),
            ref=None,
            model=None,
            instructions="keep both greetings",
            decisions={"a.txt": "theirs"},
            ask=None,
        )

    # Assert: a.txt was taken by git, only b.txt reached the agent, with the instructions.
    assert out["success"] is True
    assert (work / "a.txt").read_text() == "a main\n"
    assert "- b.txt:" in tasks[0] and "- a.txt:" not in tasks[0]
    assert "Instructions from the user: keep both greetings" in tasks[0]


def test_normalize_decision_maps_labels_and_keeps_free_text() -> None:
    # Arrange / Act / Assert
    assert normalize_decision("Keep ours (feature): a feature") == "ours"
    assert normalize_decision("Take theirs (main): a main") == "theirs"
    assert normalize_decision("Combine both with the coding agent") == "combine"
    assert normalize_decision("main") == "theirs"
    assert normalize_decision("keep the header from main, the body from ours") == (
        "keep the header from main, the body from ours"
    )
