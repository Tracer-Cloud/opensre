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
    bare = tmp_path / "origin.git"
    _git(tmp_path, "init", "--bare", str(bare))
    _git(work, "remote", "add", "origin", str(bare))
    _git(work, "push", "-q", "-u", "origin", "feature")
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
    assert text.index("(resolving)") < text.index("merged result", text.index("(resolving)"))


def test_undecided_files_open_the_menu_when_the_user_wants_to_decide_per_file(
    tmp_path: Path,
) -> None:
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
            str(work),
            ref=None,
            model=None,
            instructions=None,
            decisions={"*": "Decide file by file", "a.txt": "ours"},
            ask=ask,
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


def test_files_already_resolved_in_the_tree_are_not_sent_to_the_agent_again(
    tmp_path: Path,
) -> None:
    # Arrange: a.txt was combined by an earlier run; b.txt still holds markers.
    work = _stopped_merge_two_files(tmp_path)
    (work / "a.txt").write_text("a feature and main\n")
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
            instructions=None,
            decisions={"a.txt": "combine", "b.txt": "combine"},
            ask=None,
        )

    # Assert
    assert out["success"] is True
    assert len(tasks) == 1 and "- b.txt:" in tasks[0] and "- a.txt:" not in tasks[0]
    assert (work / "a.txt").read_text() == "a feature and main\n"


def test_several_files_are_asked_once_and_the_answer_applies_to_all(tmp_path: Path) -> None:
    # Arrange
    work = _stopped_merge_two_files(tmp_path)

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=_never_run):
        out = resolve_merge(
            str(work),
            ref=None,
            model=None,
            instructions=None,
            decisions={"*": "Take theirs for all"},
            approve=None,
        )

    # Assert
    assert out["success"] is True
    assert (work / "a.txt").read_text() == "a main\n"
    assert (work / "b.txt").read_text() == "b main\n"


def test_files_the_agent_could_not_settle_are_asked_through_the_menu(tmp_path: Path) -> None:
    # Arrange: the agent combines a.txt but leaves b.txt with markers.
    work = _stopped_merge_two_files(tmp_path)
    asked: list[list[str]] = []

    def combine(_task: str, **_kwargs: object) -> CodingResult:
        (work / "a.txt").write_text("a feature and main\n")
        return CodingResult(success=True, summary="b.txt needs a decision.")

    def ask(choices: list[FileChoice]) -> bool:
        asked.append([c.path for c in choices])
        return True

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=combine):
        out = resolve_merge(
            str(work),
            ref=None,
            model=None,
            instructions=None,
            decisions={"*": "combine"},
            ask=ask,
        )

    # Assert: the tool itself queues the menu for b.txt only.
    assert asked == [["b.txt"]]
    assert out["error_kind"] == "awaiting_decisions" and out["menu"] == "queued"
    assert merge_in_progress(str(work))


def test_by_default_every_open_file_goes_to_the_coding_agent_without_a_question(
    tmp_path: Path,
) -> None:
    # Arrange
    work = _stopped_merge_two_files(tmp_path)
    tasks: list[str] = []

    def combine(task: str, **_kwargs: object) -> CodingResult:
        tasks.append(task)
        (work / "a.txt").write_text("a feature and main\n")
        (work / "b.txt").write_text("b feature and main\n")
        return CodingResult(success=True, summary="Combined both files.")

    def never_ask(_choices: list[FileChoice]) -> bool:
        raise AssertionError("no menu before the agent unless the user asked for it")

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=combine):
        out = resolve_merge(
            str(work), ref=None, model=None, instructions=None, ask=never_ask, approve=None
        )

    # Assert
    assert out["success"] is True and len(tasks) == 1
    assert "- a.txt:" in tasks[0] and "- b.txt:" in tasks[0]


def test_a_fresh_delete_modify_conflict_is_asked_not_silently_kept(tmp_path: Path) -> None:
    # Arrange: main deleted a file that feature changed; the file carries no markers.
    work = tmp_path / "work"
    _git(tmp_path, "init", "-b", "main", str(work))
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "doomed.txt").write_text("keep?\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "checkout", "-b", "feature")
    (work / "doomed.txt").write_text("changed on feature\n")
    _git(work, "commit", "-am", "feature")
    _git(work, "checkout", "main")
    _git(work, "rm", "-q", "doomed.txt")
    _git(work, "commit", "-m", "main deletes")
    _git(work, "checkout", "feature")
    subprocess.run(["git", "merge", "main"], cwd=work, capture_output=True, text=True)
    before = head_sha(str(work))
    asked: list[list[str]] = []

    def ask(choices: list[FileChoice]) -> bool:
        asked.append([c.path for c in choices])
        return True

    def agent_leaves_it(_task: str, **_kwargs: object) -> CodingResult:
        return CodingResult(success=True, summary="A person must decide about doomed.txt.")

    # Act
    with patch(_VERIFY, return_value=(True, "ready")), patch(_RUN, side_effect=agent_leaves_it):
        out = resolve_merge(str(work), ref=None, model=None, instructions=None, ask=ask)

    # Assert: the agent could not settle it, so the tool asks with both sides.
    assert asked == [["doomed.txt"]]
    assert out["error_kind"] == "awaiting_decisions"
    assert head_sha(str(work)) == before and merge_in_progress(str(work))
