"""Tests for the first-experience demo picker."""

from __future__ import annotations

import io
import json
from collections.abc import Iterator
from pathlib import Path

import pytest
from rich.console import Console

import surfaces.interactive_shell.runtime.startup.demo_picker as demo_picker
from surfaces.interactive_shell.session import Session
from tools.system.workspace_git_scan.scan import RepoActivity, WorkspaceSnapshot


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


def _repo(name: str, github: str, *, commits: int, own: int, workflows: bool) -> RepoActivity:
    owner, _, repo = github.partition("/")
    return RepoActivity(
        name=name,
        path=f"/home/u/{name}",
        origin=f"https://github.com/{github}",
        github_owner=owner,
        github_repo=repo,
        commits=commits,
        own_commits=own,
        uncommitted=0,
        has_workflows=workflows,
    )


_SNAPSHOT = WorkspaceSnapshot(
    root="/home/u",
    days=30,
    repos=(
        _repo("busy-team-repo", "acme/busy", commits=900, own=0, workflows=True),
        _repo("mine", "me/mine", commits=120, own=110, workflows=True),
        _repo("no-ci", "me/no-ci", commits=300, own=300, workflows=False),
        _repo("local-only", "", commits=50, own=50, workflows=True),
    ),
)


def _offerable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    marker = tmp_path / "onboarding_demo.json"
    monkeypatch.setattr(demo_picker, "marker_path", lambda: marker)
    monkeypatch.setattr(demo_picker, "is_test_run", lambda: False)
    monkeypatch.setattr(demo_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_prompted", lambda: None)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_skipped", lambda: None)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_selected", lambda **_kw: None)
    monkeypatch.setattr(demo_picker, "scan_workspace", lambda *_a, **_kw: _SNAPSHOT)
    monkeypatch.setattr(demo_picker, "resolve_github_token", lambda _token: "tok")
    return marker


def _answers(monkeypatch: pytest.MonkeyPatch, *values: str | None) -> list[dict]:
    """Script successive menu answers and record each menu's choices."""
    calls: list[dict] = []
    answers: Iterator[str | None] = iter(values)

    def choose(**kwargs: object) -> str | None:
        calls.append(kwargs)
        return next(answers)

    monkeypatch.setattr(demo_picker, "repl_choose_one", choose)
    return calls


def test_analytics_demo_scans_asks_for_the_repository_then_queues_the_analysis(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange
    marker = _offerable(monkeypatch, tmp_path)
    calls = _answers(monkeypatch, demo_picker.OPTION_CI_ANALYTICS, "me/mine[v2]")
    session = Session()
    console, buf = _capture()

    # Act
    queued = demo_picker.offer_demo(session, console)

    # Assert: chart painted, own repositories with CI first, example last, prompt names the pick.
    assert queued is True
    output = buf.getvalue()
    assert "live snapshot built from your machine" in output
    assert "Activity (commits, last 30 days)" in output
    assert "Analyzing the CI/CD reliability of me/mine[v2]" in output
    demo_labels = [label for _value, label in calls[0]["choices"]]
    assert demo_labels[-1] == "Or type your own answer..."
    assert calls[0]["header"] == "Ask User"
    assert calls[1]["header"] == "Ask User"
    repo_choices = [value for value, _label in calls[1]["choices"]]
    assert repo_choices == ["me/mine", "acme/busy", demo_picker.EXAMPLE_REPOSITORY, "custom"]
    assert session.terminal.pending_prompt_autosubmit is True
    assert session.terminal.pending_prompt_plain_turn is True
    assert "me/mine[v2]" in session.terminal.pending_prompt_default
    assert json.loads(marker.read_text())["option"] == demo_picker.OPTION_CI_ANALYTICS


def test_analytics_demo_stops_with_setup_hint_when_no_github_token(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    monkeypatch.setattr(demo_picker, "resolve_github_token", lambda _token: "")
    calls = _answers(monkeypatch, demo_picker.OPTION_CI_ANALYTICS)
    session = Session()
    console, buf = _capture()

    queued = demo_picker.offer_demo(session, console)

    assert queued is False
    assert len(calls) == 1
    assert "opensre integrations setup github" in buf.getvalue()
    assert not session.terminal.pending_prompt_default
    assert not marker.is_file()
    assert demo_picker.should_offer_demo() is True


def test_cancelled_repository_pick_does_not_record_the_demo(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, demo_picker.OPTION_CI_ANALYTICS, None)
    session = Session()

    queued = demo_picker.offer_demo(session, None)

    assert queued is False
    assert not session.terminal.pending_prompt_default
    assert not marker.is_file()
    assert demo_picker.should_offer_demo() is True


def test_other_demos_queue_their_prompt_directly(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, demo_picker.OPTION_SLACK)
    session = Session()

    assert demo_picker.offer_demo(session, None) is True
    assert "Slack" in session.terminal.pending_prompt_default
    assert session.terminal.pending_prompt_plain_turn is False


def test_typed_answer_is_submitted_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, "show me my flaky tests")
    session = Session()

    assert demo_picker.offer_demo(session, None) is True
    assert session.terminal.pending_prompt_default == "show me my flaky tests"


def test_custom_row_submitted_empty_counts_as_skip(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, "custom")
    session = Session()

    assert demo_picker.offer_demo(session, None) is False
    assert not session.terminal.pending_prompt_default


def test_skip_records_the_marker_so_the_picker_shows_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    _answers(monkeypatch, None)
    session = Session()

    first = demo_picker.offer_demo(session, None)

    assert first is False
    assert not session.terminal.pending_prompt_default
    assert marker.is_file()
    assert demo_picker.should_offer_demo() is False


def test_force_reopens_the_picker_after_it_was_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    marker.write_text('{"option": "skipped"}')
    _answers(monkeypatch, demo_picker.OPTION_SLACK)
    session = Session()

    assert demo_picker.offer_demo(session, None) is False
    assert demo_picker.offer_demo(session, None, force=True) is True
