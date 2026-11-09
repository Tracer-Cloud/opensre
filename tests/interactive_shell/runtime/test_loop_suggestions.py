"""Tests for the suggested-loops startup picker."""

from __future__ import annotations

import io

import pytest
from rich.console import Console

import surfaces.interactive_shell.runtime.startup.loop_suggestions as loop_suggestions
from surfaces.interactive_shell.session import Session


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


def _offerable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(loop_suggestions, "should_offer_loop_suggestions", lambda: True)
    monkeypatch.setattr(loop_suggestions, "capture_loop_suggestion_prompted", lambda: None)
    monkeypatch.setattr(loop_suggestions, "capture_loop_suggestion_skipped", lambda: None)
    monkeypatch.setattr(loop_suggestions, "capture_loop_suggestion_selected", lambda **_kw: None)


def test_picker_explains_what_a_loop_is_inside_the_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The explainer lives in the menu so skip/select does not leave it in the transcript."""
    _offerable(monkeypatch)
    seen: dict[str, object] = {}

    def choose(**kwargs: object) -> None:
        seen.update(kwargs)
        return None

    monkeypatch.setattr(loop_suggestions, "repl_choose_one", choose)
    console, buf = _capture()

    loop_suggestions.offer_loop_suggestions(Session(), console)

    assert "scheduled check-ins" in str(seen.get("note", ""))
    assert "nothing is saved until you confirm" in str(seen.get("note", ""))
    assert "/loops" in str(seen.get("note", ""))
    assert "scheduled check-ins" not in buf.getvalue()


def test_selection_queues_the_canned_prompt(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _offerable(monkeypatch)
    monkeypatch.setattr(
        loop_suggestions, "repl_choose_one", lambda **_kw: loop_suggestions.OPTION_DAILY_BRIEF
    )
    session = Session()
    console, _buf = _capture()

    # Act
    loop_suggestions.offer_loop_suggestions(session, console)

    # Assert
    assert session.terminal.pending_prompt_autosubmit is True
    assert "morning report" in session.terminal.pending_prompt_default


def test_ci_suggestion_runs_the_deterministic_agent_demo_without_a_prompt(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the CI/CD row must not hand a model a prompt it cannot fulfil.
    _offerable(monkeypatch)
    monkeypatch.setattr(
        loop_suggestions, "repl_choose_one", lambda **_kw: loop_suggestions.OPTION_CI_CD
    )
    started: list[object] = []
    monkeypatch.setattr(
        loop_suggestions, "start_ci_agent_demo", lambda session, _console: started.append(session)
    )
    session = Session()

    # Act
    loop_suggestions.offer_loop_suggestions(session, None)

    # Assert
    assert started == [session]
    assert session.terminal.pending_prompt_autosubmit is False
    assert not session.terminal.pending_prompt_default


def test_skip_queues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    _offerable(monkeypatch)
    monkeypatch.setattr(loop_suggestions, "repl_choose_one", lambda **_kw: None)
    session = Session()

    # Act
    loop_suggestions.offer_loop_suggestions(session, None)

    # Assert
    assert session.terminal.pending_prompt_autosubmit is False
    assert not session.terminal.pending_prompt_default
