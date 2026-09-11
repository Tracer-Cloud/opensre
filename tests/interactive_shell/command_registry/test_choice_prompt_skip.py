"""The master menu's skip row is a shell decision, not an answer for the model."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from config.constants.skills import SKIP_DEMO_OPTION
from core.agent_harness.session.pending_choice import PendingUserChoice
from surfaces.interactive_shell.command_registry import choice_prompt
from surfaces.interactive_shell.session import Session


def _pick_skip(**_kwargs: Any) -> str:
    return SKIP_DEMO_OPTION


def test_skip_option_opens_the_plain_shell_without_a_model_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the onboarding menu is pending and the picker returns the skip row.
    session = Session()
    session.active_skill = "onboarding-github-ci"
    session.pending_user_choice = PendingUserChoice(
        title="Which demo would you like me to run?",
        options=("Explore a repo", SKIP_DEMO_OPTION),
    )
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _pick_skip)
    monkeypatch.setattr(choice_prompt, "clear_live_prompt_paint", lambda _session: None)
    monkeypatch.setattr(choice_prompt, "play_notification", lambda _event: None)
    monkeypatch.setattr(choice_prompt, "capture_onboarding_choice", lambda *_a, **_k: None)
    console = Console(file=io.StringIO(), force_terminal=False, width=100)

    # Act
    handled = choice_prompt._cmd_choose(session, console, [])

    # Assert: nothing queued for the model, skill left, plain prompt next.
    assert handled is True
    assert session.pending_user_choice is None
    assert session.active_skill is None
    assert session.terminal.awaiting_handoff_answer is False
    assert session.terminal.pending_prompt_default in (None, "")
    assert "Demo skipped" in console.file.getvalue()  # type: ignore[union-attr]
