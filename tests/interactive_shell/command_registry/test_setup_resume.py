"""A turn parked behind integration setup is replayed once, and only when setup worked.

The setup wizard reports success for every interactive run, so the resume
re-checks the prerequisite itself. Each test pins one rule of that replay.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from config.constants import GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV
from config.constants.skills import ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
from core.agent_harness.spi.handoff import AskUserQuestion, format_ask_user_answers
from core.agent_harness.spi.session_state import arm_setup_resume, pending_setup_resume
from surfaces.interactive_shell.command_registry.setup_resume import (
    ResumeOutcome,
    resume_after_setup,
)
from surfaces.interactive_shell.runtime.input.actions import SubmitTurn
from surfaces.interactive_shell.session import Session

_SKILL = ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
_ANSWER = format_ask_user_answers(
    (AskUserQuestion(label="", title="Which repository should I analyze?", options=()),),
    ("acme/widget",),
)


@pytest.fixture(autouse=True)
def _no_ambient_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (GITHUB_TOKEN_ENV, GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)


def _session(parked: str) -> Session:
    session = Session()
    session.resolved_integrations_cache = {}
    assert arm_setup_resume(session, parked, skill=_SKILL, service="github")
    return session


def _console() -> Console:
    return Console(file=io.StringIO(), force_terminal=False, highlight=False)


@pytest.mark.parametrize(
    ("parked", "plain_turn", "awaiting_answer"),
    [(_ANSWER, False, True), ("analyze the CI of acme/widget", True, False)],
    ids=["menu-answer", "request"],
)
def test_the_parked_turn_is_replayed_once_after_the_token_resolves(
    monkeypatch: pytest.MonkeyPatch, parked: str, plain_turn: bool, awaiting_answer: bool
) -> None:
    """A menu answer goes back as ``/choose`` submits it, so it keeps its skill context."""
    session = _session(parked)
    monkeypatch.setenv(GH_TOKEN_ENV, "env-tok")
    terminal = session.terminal

    first = resume_after_setup(session, _console(), service="github")

    assert first is ResumeOutcome.REPLAYED
    assert terminal.pending_prompt_default == parked
    assert terminal.pending_prompt_autosubmit is True
    assert terminal.pending_prompt_plain_turn is plain_turn
    assert terminal.awaiting_handoff_answer is awaiting_answer
    # The prompt submits it; a second setup must not replay it again.
    terminal.pop_pending_prompt_default()
    terminal.pop_pending_autosubmit()
    assert resume_after_setup(session, _console(), service="github") is (
        ResumeOutcome.NOTHING_PARKED
    )
    assert terminal.pending_prompt_default is None


def test_a_queued_autosubmit_is_never_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _session(_ANSWER)
    monkeypatch.setenv(GH_TOKEN_ENV, "env-tok")
    session.terminal.set_auto_command("/integrations verify github")

    outcome = resume_after_setup(session, _console(), service="github")

    assert outcome is ResumeOutcome.SLOT_BUSY
    assert session.terminal.pending_prompt_default == "/integrations verify github"
    assert pending_setup_resume(session) is not None


def test_a_setup_that_left_no_token_keeps_the_turn_parked_and_asks_again() -> None:
    session = _session(_ANSWER)

    outcome = resume_after_setup(session, _console(), service="github")

    assert outcome is ResumeOutcome.STILL_MISSING
    assert pending_setup_resume(session) is not None
    pending = session.pending_user_choice
    assert pending is not None
    assert pending.note == "GitHub is still not connected: no usable token was found."
    assert session.terminal.pending_prompt_default == "/choose"


@pytest.mark.asyncio
async def test_a_typed_turn_drops_the_parked_turn_but_an_autosubmitted_one_keeps_it() -> None:
    """Typing moves on; the ``/choose`` and setup turns between park and resume are autosubmitted."""
    from surfaces.interactive_shell.controller import InteractiveShellController

    session = _session(_ANSWER)
    controller = InteractiveShellController(session, console=_console())

    session.terminal.last_input_autosubmitted = True
    await controller._handle_input_action(SubmitTurn(text="/integrations setup github"))
    kept = pending_setup_resume(session)
    await controller._handle_input_action(SubmitTurn(text="what changed in main?"))

    assert kept is not None
    assert pending_setup_resume(session) is None
