"""Startup executes the master skill before asking and continuing a child skill."""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

import surfaces.interactive_shell.command_registry.choice_prompt as choice_prompt
import surfaces.interactive_shell.runtime.slash_adapter as slash_adapter
import surfaces.interactive_shell.runtime.startup.demo_picker as demo_picker
import surfaces.interactive_shell.runtime.startup.onboarding_telemetry as onboarding_telemetry
import tools.system.workspace_git_scan.tool as scan_tool
from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.prompts.action.assemble import build_action_system_prompt_envelope
from core.agent_harness.prompts.getting_started import GETTING_STARTED_OPTIONS
from core.agent_harness.prompts.skills import list_action_skills
from core.agent_harness.session.pending_choice import PendingUserChoice, format_ask_user_answers
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.ask_user import CUSTOM_OPTION
from surfaces.shared.terminal.components import choice_menu, cpr_stdin
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    tool_response,
)
from tools.system.workspace_git_scan.scan import WorkspaceSnapshot

_TITLE = "Which demo would you like me to run? (Esc to skip)"
_NOTE = "Choose a demo using your own repositories or connect your team through Slack."


def _offerable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_picker, "is_test_run", lambda: False)
    monkeypatch.setattr(demo_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_prompted", lambda: None)
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(slash_adapter, "repl_tty_interactive", lambda: True)


def _take_prompt(session: Session) -> str:
    assert session.terminal.pop_pending_autosubmit()
    return session.terminal.pop_pending_prompt_default()


@pytest.fixture
def onboarding_outcomes(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, bool | None]]:
    outcomes: list[tuple[str, bool | None]] = []

    def selected(*, option: str, custom: bool) -> None:
        outcomes.append((option, custom))

    def skipped() -> None:
        outcomes.append(("skipped", None))

    monkeypatch.setattr(onboarding_telemetry, "capture_onboarding_demo_selected", selected)
    monkeypatch.setattr(onboarding_telemetry, "capture_onboarding_demo_skipped", skipped)
    return outcomes


def test_startup_skill_asks_once_and_selected_child_runs_through_real_turns(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.resolved_integrations_cache = {}
    console = Console(file=io.StringIO(), highlight=False)
    llm = FakeActionLLM(
        [
            tool_response("skill_view", {"name": ONBOARDING_SKILL_NAME}),
            tool_response(
                "ask_user_choice",
                {"title": _TITLE, "options": list(GETTING_STARTED_OPTIONS), "note": _NOTE},
            ),
            tool_response("skill_view", {"name": "cicd-analytics-demo"}),
            tool_response("scan_local_git_workspace"),
            tool_response(
                "ask_user_choice",
                {
                    "title": "Which repository should I analyze?",
                    "options": ["acme/one", "acme/two"],
                },
            ),
        ]
    )
    scans: list[str] = []

    def scan(root: Any, **_kwargs: Any) -> WorkspaceSnapshot:
        scans.append(str(root))
        return WorkspaceSnapshot(root=str(root), days=30, repos=())

    def pick(**kwargs: Any) -> str:
        assert kwargs["header"] == "Ask User"
        assert kwargs["note"] == _NOTE
        assert kwargs["choices"] == [
            *((option, option) for option in GETTING_STARTED_OPTIONS),
            (CUSTOM_OPTION, CUSTOM_OPTION),
        ]
        return GETTING_STARTED_OPTIONS[0]

    monkeypatch.setattr(scan_tool, "scan_workspace", scan)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    assert demo_picker.offer_demo(session, console)
    assert session.pending_user_choice is None  # The host has not asked the question.

    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: llm
    )
    assert llm.invocations == 2  # ask_user_choice terminates the model turn immediately.
    assert session.active_skill == ONBOARDING_SKILL_NAME
    pending = session.pending_user_choice
    assert pending is not None and pending.options == GETTING_STARTED_OPTIONS

    assert session.terminal.pending_prompt_default == "/choose"
    # The controller reserves stdin for literal /choose before dispatching the turn.
    session.terminal.exclusive_stdin_active = True
    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: llm
    )
    session.terminal.exclusive_stdin_active = False
    assert llm.invocations == 2  # Literal /choose uses no model.
    assert session.active_skill == ONBOARDING_SKILL_NAME
    answer = _take_prompt(session)
    assert answer == format_ask_user_answers(pending.items(), (GETTING_STARTED_OPTIONS[0],))
    envelope = build_action_system_prompt_envelope(
        TurnSnapshot.from_session(answer, session, surface="interactive_shell")
    )
    assert "## Follow the selected child" in envelope.render_ephemeral()
    assert "## Follow the selected child" not in envelope.render_cached()

    run_action_tool_turn(answer, session, console, is_tty=True, llm_factory=lambda: llm)
    assert llm.invocations == 5
    assert len(scans) == 1
    assert session.active_skill == "cicd-analytics-demo"
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == "Which repository should I analyze?"
    assert "analyze_github_ci_reliability" in session.active_skill_tools
    assert onboarding_outcomes == [("ci_analytics", False)]


@pytest.mark.parametrize("answer", [None, "Inspect the deployment logs", "/help"])
def test_onboarding_cancel_custom_and_slash_do_not_reopen_the_menu(
    monkeypatch: pytest.MonkeyPatch,
    answer: str | None,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.pending_user_choice = PendingUserChoice(title=_TITLE, options=GETTING_STARTED_OPTIONS)
    pending = session.pending_user_choice
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: answer)
    console = Console(file=io.StringIO())

    choice_prompt._cmd_choose(session, console, [])

    assert session.pending_user_choice is None
    assert onboarding_outcomes == [("skipped", None) if answer is None else ("custom", True)]
    if answer is None:
        assert session.terminal.pending_prompt_default is None
        assert session.active_skill is None
        assert not session.terminal.awaiting_handoff_answer
    elif answer.startswith("/"):
        assert _take_prompt(session) == answer
    else:
        assert _take_prompt(session) == format_ask_user_answers(pending.items(), (answer,))
        assert session.active_skill_tools == ()  # Custom requests have the full tool catalog.


def test_onboarding_outcomes_keep_stable_ids_and_exclude_child_menus(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    _offerable(monkeypatch)
    console = Console(file=io.StringIO())
    answer = ""

    def pick(**_kwargs: Any) -> str:
        return answer

    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    session = Session()
    for option in GETTING_STARTED_OPTIONS:
        answer = option
        session.active_skill = ONBOARDING_SKILL_NAME
        session.pending_user_choice = PendingUserChoice(
            title=_TITLE, options=GETTING_STARTED_OPTIONS
        )
        choice_prompt._cmd_choose(session, console, [])

    session.active_skill = "cicd-analytics-demo"
    session.pending_user_choice = PendingUserChoice(title="Repository?", options=("acme/one",))
    answer = "acme/one"
    choice_prompt._cmd_choose(session, console, [])
    assert onboarding_outcomes == [
        ("ci_analytics", False),
        ("ci_agent", False),
        ("remote_managed_service", False),
        ("slack", False),
    ]


def test_onboarding_telemetry_failure_does_not_lose_the_answer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    pending = PendingUserChoice(title=_TITLE, options=GETTING_STARTED_OPTIONS)
    session.pending_user_choice = pending

    def fail_capture(**_kwargs: Any) -> None:
        raise RuntimeError("Telemetry unavailable")

    answer = GETTING_STARTED_OPTIONS[0]
    monkeypatch.setattr(onboarding_telemetry, "capture_onboarding_demo_selected", fail_capture)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: answer)
    choice_prompt._cmd_choose(session, Console(file=io.StringIO()), [])
    assert _take_prompt(session) == format_ask_user_answers(pending.items(), (answer,))
    assert session.active_skill == ONBOARDING_SKILL_NAME


@pytest.mark.parametrize("typed", [False, True])
def test_typed_option_label_keeps_its_custom_source_through_the_picker(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
    typed: bool,
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    pending = PendingUserChoice(title=_TITLE, options=GETTING_STARTED_OPTIONS)
    session.pending_user_choice = pending
    answer = GETTING_STARTED_OPTIONS[0]

    def pick(**_kwargs: Any) -> int | str:
        # The raw picker distinguishes a row index from text typed in the custom row.
        return answer if typed else 0

    monkeypatch.setattr(choice_menu, "_pick", pick)
    monkeypatch.setattr(choice_menu, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_menu, "_clear_prompt_toolkit_paint", lambda: None)
    monkeypatch.setattr(choice_menu, "hide_terminal_cursor", lambda: None)
    monkeypatch.setattr(choice_menu, "leave_inline_menu", lambda: None)
    monkeypatch.setattr(cpr_stdin, "drain_stale_cpr_bytes", lambda: None)
    choice_prompt._cmd_choose(session, Console(file=io.StringIO()), [])
    assert _take_prompt(session) == format_ask_user_answers(pending.items(), (answer,))
    assert onboarding_outcomes == [("custom", True) if typed else ("ci_analytics", False)]


def test_startup_and_demo_respect_tty_and_pending_input(monkeypatch: pytest.MonkeyPatch) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.terminal.set_auto_command("/resume existing")
    assert not demo_picker.offer_demo(session, force=True)
    assert session.terminal.pending_prompt_default == "/resume existing"
    _take_prompt(session)
    session.pending_user_choice = PendingUserChoice(title="Existing", options=("One", "Two"))
    assert not demo_picker.offer_demo(session, force=True)
    session.pending_user_choice = None
    monkeypatch.setattr(demo_picker, "repl_tty_interactive", lambda: False)
    assert not demo_picker.offer_demo(session, force=True)
    monkeypatch.setattr(demo_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(demo_picker, "is_test_run", lambda: True)
    assert not demo_picker.offer_demo(session)
    assert demo_picker.offer_demo(session, force=True)
    assert ONBOARDING_SKILL_NAME in _take_prompt(session)


def test_demo_skills_keep_their_tool_contracts_after_moving() -> None:
    by_name = {skill.name: skill for skill in list_action_skills()}
    assert by_name["cicd-analytics-demo"].tools == (
        "scan_local_git_workspace",
        "analyze_github_ci_reliability",
        "schedule_ci_reliability_loop",
        "cli_exec",
        "slash_invoke",
        "ask_user_choice",
    )
    assert by_name["cicd-reliability-agent"].tools == (
        "scan_local_git_workspace",
        "schedule_ci_reliability_loop",
        "ask_user_choice",
    )
    assert by_name["slack-handoff"].tools == ("cli_exec", "slash_invoke")
