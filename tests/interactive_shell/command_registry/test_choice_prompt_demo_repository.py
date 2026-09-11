"""Picking a repository demo asks for the repository in the shell before the model runs.

Left to the model, the workspace scan was skipped and "CI/CD" was read out of
the menu row as owner/repo; the repository menu, tied to the scan, never opened.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

from config.constants.skills import ONBOARDING_SKILL_NAME, SKIP_DEMO_OPTION
from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    PendingUserChoice,
    format_ask_user_answers,
)
from surfaces.interactive_shell.command_registry import choice_prompt
from surfaces.interactive_shell.session import Session

_DEMO_QUESTION = "Which demo would you like me to run?"
# The bundled demos now ask for the repository from their own plan, so the
# host-side question is exercised through a demo that declares one.
_DEMO = "Set up an agent that improves CI/CD reliability over time"
_REPOSITORY_QUESTION = "Which repository should the agent watch?"


def _pick_demo(**_kwargs: Any) -> str:
    return _DEMO


def _declared_question(skill: Any) -> str | None:
    return _REPOSITORY_QUESTION if skill.getting_started == _DEMO else None


def _arrange(
    monkeypatch: pytest.MonkeyPatch, *, repository: str | None
) -> tuple[Session, list[str]]:
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.skills_already_prompted.add(ONBOARDING_SKILL_NAME)
    session.pending_user_choice = PendingUserChoice(
        title=_DEMO_QUESTION, options=(_DEMO, SKIP_DEMO_OPTION), custom_answer=False
    )
    monkeypatch.setattr(choice_prompt, "repository_question", _declared_question)
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", _pick_demo)
    monkeypatch.setattr(choice_prompt, "clear_live_prompt_paint", lambda _session: None)
    monkeypatch.setattr(choice_prompt, "play_notification", lambda _event: None)
    monkeypatch.setattr(choice_prompt, "capture_onboarding_choice", lambda *_a, **_k: None)
    asked: list[str] = []

    def _choose(_console: Any, question: str) -> str | None:
        asked.append(question)
        return repository

    monkeypatch.setattr(choice_prompt, "choose_demo_repository", _choose)
    return session, asked


def test_the_repository_is_asked_in_the_shell_and_travels_with_the_demo_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the onboarding menu is pending; the picker returns the reliability agent demo.
    session, asked = _arrange(monkeypatch, repository="acme/app")
    console = Console(file=io.StringIO(), force_terminal=False, width=100)

    # Act
    handled = choice_prompt._cmd_choose(session, console, [])

    # Assert: the skill's own question was asked, and both answers go to the model at once.
    assert handled is True
    assert asked == [_REPOSITORY_QUESTION]
    questions = (
        AskUserQuestion(label="", title=_DEMO_QUESTION, options=(_DEMO, SKIP_DEMO_OPTION)),
        AskUserQuestion(label="", title=_REPOSITORY_QUESTION, options=("acme/app",)),
    )
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        questions, (_DEMO, "acme/app")
    )
    assert session.terminal.awaiting_handoff_answer is True
    assert _REPOSITORY_QUESTION.lower() in session.questions_already_answered


def test_escaping_the_repository_menu_cancels_instead_of_letting_the_model_guess(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the user escapes the repository menu.
    session, _asked = _arrange(monkeypatch, repository=None)
    buffer = io.StringIO()
    console = Console(file=buffer, force_terminal=False, width=100)

    # Act
    choice_prompt._cmd_choose(session, console, [])

    # Assert: nothing is queued for the model and the skill is left.
    assert session.terminal.pending_prompt_default in (None, "")
    assert session.terminal.awaiting_handoff_answer is False
    assert session.active_skill is None
    assert ONBOARDING_SKILL_NAME not in session.skills_already_prompted
    assert _DEMO_QUESTION.lower() not in session.questions_already_answered
    assert "cancelled" in buffer.getvalue().lower()


def test_a_demo_without_a_repository_question_is_answered_as_before(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange: the Slack demo declares no repository menu.
    slack = "Connect OpenSRE to Slack and hand off DevOps chores for your team"
    session, asked = _arrange(monkeypatch, repository="unused")
    session.pending_user_choice = PendingUserChoice(
        title=_DEMO_QUESTION, options=(slack, SKIP_DEMO_OPTION), custom_answer=False
    )
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_k: slack)
    console = Console(file=io.StringIO(), force_terminal=False, width=100)

    # Act
    choice_prompt._cmd_choose(session, console, [])

    # Assert: no scan, the single answer travels alone.
    assert asked == []
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        (AskUserQuestion(label="", title=_DEMO_QUESTION, options=(slack, SKIP_DEMO_OPTION)),),
        (slack,),
    )
