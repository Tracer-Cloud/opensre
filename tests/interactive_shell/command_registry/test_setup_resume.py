"""A turn parked behind integration setup is replayed once, and only when setup worked.

The setup wizard reports success for every interactive run, so the resume
re-checks the prerequisite itself. Each test pins one rule of that replay.
"""

from __future__ import annotations

import io

import pytest
from rich.console import Console

from config.constants import GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV
from config.constants.skills import (
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    CONNECTING_SLACK_SKILL_NAME,
    DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME,
)
from config.constants.slack import (
    SLACK_APP_TOKEN_ENV,
    SLACK_BOT_TOKEN_ENV,
    SLACK_WEBHOOK_URL_ENV,
)
from config.constants.slash_commands import QUEUED_COMMAND_KEY
from core.agent_harness.spi.handoff import AskUserQuestion, format_ask_user_answers
from core.agent_harness.spi.session_state import arm_setup_resume, pending_setup_resume
from core.agent_harness.tools import ActionToolScope
from surfaces.interactive_shell.command_registry.setup_resume import (
    ResumeOutcome,
    resume_after_setup,
)
from surfaces.interactive_shell.runtime.input.actions import SubmitTurn
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import FakeSlashPorts
from tools.interactive_shell.actions.slash import execute_slash_tool

_SKILL = ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
_ANSWER = format_ask_user_answers(
    (AskUserQuestion(label="", title="Which repository should I analyze?", options=()),),
    ("acme/widget",),
)


@pytest.fixture(autouse=True)
def _no_ambient_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        GITHUB_TOKEN_ENV,
        GH_TOKEN_ENV,
        GITHUB_MCP_AUTH_TOKEN_ENV,
        SLACK_BOT_TOKEN_ENV,
        SLACK_APP_TOKEN_ENV,
        SLACK_WEBHOOK_URL_ENV,
    ):
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


def _setup_queued_mid_skill(skill: str, service: str) -> Session:
    """The agent queues ``/integrations setup <service>`` while ``skill`` is active; it runs."""
    session = Session()
    session.resolved_integrations_cache = {}
    session.active_skill = skill
    scope = ActionToolScope(
        session=session,
        console=_console(),
        slash_ports=FakeSlashPorts(tty=True),
        turn_user_message=_ANSWER,
    )
    queued = execute_slash_tool({"command": "/integrations", "args": ["setup", service]}, scope)
    assert isinstance(queued, dict) and queued[QUEUED_COMMAND_KEY]
    session.terminal.pop_pending_prompt_default()
    session.terminal.pop_pending_autosubmit()
    return session


@pytest.mark.parametrize(
    ("skill", "service", "env", "outcome"),
    [
        (CONNECTING_SLACK_SKILL_NAME, "slack", None, ResumeOutcome.STILL_MISSING),
        (CONNECTING_SLACK_SKILL_NAME, "slack", SLACK_BOT_TOKEN_ENV, ResumeOutcome.REPLAYED),
        (
            ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
            "github",
            GH_TOKEN_ENV,
            ResumeOutcome.REPLAYED,
        ),
    ],
    ids=["slack-cancelled", "slack-connected", "github"],
)
def test_only_a_setup_a_check_can_confirm_brings_the_skill_back(
    monkeypatch: pytest.MonkeyPatch,
    skill: str,
    service: str,
    env: str | None,
    outcome: ResumeOutcome,
) -> None:
    """A cancelled Slack wizard replayed the Slack demo's turn, which queued the wizard again.

    The Slack check now tells a finished setup from a cancelled one: a
    cancelled wizard puts the setup menu back, with "Not now" to leave, and
    only a resolving token brings the turn back.
    """
    session = _setup_queued_mid_skill(skill, service)
    if env is not None:
        monkeypatch.setenv(env, "xoxb-env-tok" if env == SLACK_BOT_TOKEN_ENV else "env-tok")
    session.refresh_integration_state()  # as the wizard's slash command does

    assert resume_after_setup(session, _console(), service=service) is outcome

    replayed = outcome is ResumeOutcome.REPLAYED
    assert (session.terminal.pending_prompt_default == _ANSWER) is replayed
    assert (pending_setup_resume(session) is None) is replayed
    assert (session.pending_user_choice is not None) is not replayed


def test_a_parked_turn_no_check_can_confirm_is_dropped_not_replayed() -> None:
    """The delegate demo checks its gateway itself, so no host check covers its GitHub setup."""
    session = Session()
    session.resolved_integrations_cache = {}
    assert arm_setup_resume(
        session, _ANSWER, skill=DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME, service="github"
    )

    outcome = resume_after_setup(session, _console(), service="github")

    assert outcome is ResumeOutcome.DROPPED
    assert pending_setup_resume(session) is None
    assert session.terminal.pending_prompt_default is None
    assert session.pending_user_choice is None


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
