"""Tests for the first-experience demo picker."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest
from rich.console import Console

import surfaces.interactive_shell.runtime.startup.demo_picker as demo_picker
from surfaces.interactive_shell.session import Session


def _capture() -> tuple[Console, io.StringIO]:
    buf = io.StringIO()
    return Console(file=buf, force_terminal=False, highlight=False, width=120), buf


def _offerable(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    marker = tmp_path / "onboarding_demo.json"
    monkeypatch.setattr(demo_picker, "marker_path", lambda: marker)
    monkeypatch.setattr(demo_picker, "is_test_run", lambda: False)
    monkeypatch.setattr(demo_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_prompted", lambda: None)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_skipped", lambda: None)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_selected", lambda **_kw: None)
    return marker


def test_selection_queues_the_demo_prompt_and_records_the_choice(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange
    marker = _offerable(monkeypatch, tmp_path)
    monkeypatch.setattr(
        demo_picker, "repl_choose_one", lambda **_kw: demo_picker.OPTION_CI_ANALYTICS
    )
    session = Session()
    console, buf = _capture()

    # Act
    queued = demo_picker.offer_demo(session, console)

    # Assert
    assert queued is True
    assert session.terminal.pending_prompt_autosubmit is True
    assert "CI/CD analytics demo" in session.terminal.pending_prompt_default
    assert json.loads(marker.read_text())["option"] == demo_picker.OPTION_CI_ANALYTICS
    assert "real from your machine" in buf.getvalue()


def test_typed_answer_is_submitted_verbatim(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _offerable(monkeypatch, tmp_path)
    monkeypatch.setattr(demo_picker, "repl_choose_one", lambda **_kw: "show me my flaky tests")
    session = Session()

    assert demo_picker.offer_demo(session, None) is True
    assert session.terminal.pending_prompt_default == "show me my flaky tests"


def test_skip_records_the_marker_so_the_picker_shows_once(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Arrange
    marker = _offerable(monkeypatch, tmp_path)
    monkeypatch.setattr(demo_picker, "repl_choose_one", lambda **_kw: None)
    session = Session()

    # Act
    first = demo_picker.offer_demo(session, None)
    second_should_offer = demo_picker.should_offer_demo()

    # Assert
    assert first is False
    assert not session.terminal.pending_prompt_default
    assert marker.is_file()
    assert second_should_offer is False


def test_force_reopens_the_picker_after_it_was_recorded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    marker = _offerable(monkeypatch, tmp_path)
    marker.write_text('{"option": "skipped"}')
    monkeypatch.setattr(demo_picker, "repl_choose_one", lambda **_kw: demo_picker.OPTION_SLACK)
    session = Session()

    assert demo_picker.offer_demo(session, None) is False
    assert demo_picker.offer_demo(session, None, force=True) is True
    assert "Slack" in session.terminal.pending_prompt_default
