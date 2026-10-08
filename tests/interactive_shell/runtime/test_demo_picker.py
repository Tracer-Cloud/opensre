"""Startup enters the master skill host-side: its menu opens before any model step."""

from __future__ import annotations

import io
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from rich.console import Console

import integrations.account_integrations as account_integrations
import integrations.github.tools.ci_analytics.analysis as ci_analysis
import integrations.github.tools.ci_analytics.tool as ci_tool
import surfaces.interactive_shell.command_registry.choice_prompt as choice_prompt
import surfaces.interactive_shell.command_registry.integrations as integrations_cmds
import surfaces.interactive_shell.command_registry.prerequisite_menu as prerequisite_menu
import surfaces.interactive_shell.runtime.slash_adapter as slash_adapter
import surfaces.interactive_shell.runtime.startup.analysis_prefetch as analysis_prefetch
import surfaces.interactive_shell.runtime.startup.demo_picker as demo_picker
import surfaces.interactive_shell.runtime.startup.onboarding_telemetry as onboarding_telemetry
import tools.interactive_shell.actions.skill_prerequisite_gate as gate
import tools.system.workspace_git_scan.tool as scan_tool
from config.account import AccountRecord
from config.constants import (
    GH_TOKEN_ENV,
    GITHUB_MCP_AUTH_TOKEN_ENV,
    GITHUB_TOKEN_ENV,
    INTEGRATIONS_STORE_PATH_ENV,
)
from config.constants.skills import (
    ANALYZE_REPO_OPTION,
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    AUTOMATION_GROUP_OPTION,
    AUTOMATION_MENU_OPTIONS,
    AUTOMATION_MENU_TITLE,
    CLOUD_REPAIR_OPTION,
    CONNECTING_SLACK_SKILL_NAME,
    DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME,
    DEMO_REPO_DECLINE_OPTION,
    DEMO_REPO_PERMISSION_TITLE,
    LOCAL_REPAIR_OPTION,
    ONBOARDING_MENU_TITLE,
    ONBOARDING_SKILL_NAME,
    OUTCOME_MENU_OPTIONS,
    SKIP_DEMO_OPTION,
    SLACK_OPTION,
)
from config.constants.slack import (
    SLACK_APP_TOKEN_ENV,
    SLACK_BOT_TOKEN_ENV,
    SLACK_WEBHOOK_URL_ENV,
)
from config.constants.tracer import TRACER_JWT_TOKEN_ENV
from core.agent_harness.prompts.action.assemble import build_action_system_prompt_envelope
from core.agent_harness.prompts.getting_started import getting_started_options
from core.agent_harness.session.pending_choice import (
    AskUserQuestion,
    PendingUserChoice,
    format_ask_user_answers,
)
from core.agent_harness.spi.session_state import pending_setup_resume
from core.agent_harness.turns.turn_snapshot import TurnSnapshot
from integrations.github.tools.ci_analytics.collector import CollectedRuns
from integrations.store import resolve_store_path, upsert_integration
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.input_prompt.rendering import render_submitted_prompt
from surfaces.shared.terminal.components import choice_menu, cpr_stdin
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    no_tool_response,
    tool_response,
)
from tools.system.workspace_git_scan.scan import WorkspaceSnapshot

_TITLE = ONBOARDING_MENU_TITLE
_REPOSITORY_TITLE = "Which repository should I analyze?"
_REPOSITORY = "acme/one"
_REPOSITORY_OPTIONS = (_REPOSITORY, "Tracer-Cloud/opensre")
_NOTE = ""
#: Turn integrations with a Slack workspace already connected.
_SLACK_CONNECTED = {"slack": {"bot_token": "xoxb-connected"}}
_SLACK_SETUP_TITLE = "Connect Slack to continue"
_SLACK_OPEN_APP = "Connect Slack in the OpenSRE app (recommended)"
_SLACK_CONTINUE = "I've connected Slack — continue"


def _offerable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(demo_picker, "is_test_run", lambda: False)
    monkeypatch.setattr(demo_picker, "is_onboarding_enabled", lambda: True)
    monkeypatch.setattr(demo_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(demo_picker, "capture_onboarding_demo_prompted", lambda: None)
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(slash_adapter, "repl_tty_interactive", lambda: True)


def test_startup_demo_is_suppressed_when_onboarding_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(demo_picker, "is_test_run", lambda: False)
    monkeypatch.setattr(demo_picker, "is_onboarding_enabled", lambda: False)
    monkeypatch.setattr(demo_picker, "repl_tty_interactive", lambda: True)

    assert demo_picker.should_offer_demo() is False


def test_explicit_demo_still_opens_when_startup_onboarding_is_disabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offerable(monkeypatch)
    monkeypatch.setattr(demo_picker, "is_onboarding_enabled", lambda: False)
    session = Session()

    assert demo_picker.offer_demo(session, force=True)


def _take_prompt(session: Session) -> str:
    assert session.terminal.pop_pending_autosubmit()
    return session.terminal.pop_pending_prompt_default()


def _no_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (GITHUB_TOKEN_ENV, GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)


def _run_slash_turn(session: Session, console: Console, command: str) -> None:
    """Run a literal slash command the way the controller does: with stdin reserved."""
    session.terminal.exclusive_stdin_active = True
    try:
        run_action_tool_turn(command, session, console, is_tty=True)
    finally:
        session.terminal.exclusive_stdin_active = False


@pytest.mark.parametrize("selection", [SKIP_DEMO_OPTION, None], ids=["skip", "escape"])
def test_demo_request_after_abandoning_startup_reopens_the_menu(
    monkeypatch: pytest.MonkeyPatch, selection: str | None
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.resolved_integrations_cache = {}
    session.skills_already_prompted.add("another-skill")
    session.questions_already_answered.add("another question?")
    console = Console(file=io.StringIO(), highlight=False)
    llm = FakeActionLLM([tool_response("skill_view", {"name": ONBOARDING_SKILL_NAME})])

    def pick(**_kwargs: Any) -> str | None:
        return selection

    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    monkeypatch.setattr(choice_prompt, "capture_onboarding_choice", lambda *_a, **_k: None)
    assert demo_picker.offer_demo(session, console)
    session.terminal.exclusive_stdin_active = True
    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: llm
    )
    session.terminal.exclusive_stdin_active = False
    assert llm.invocations == 0
    assert session.active_skill is None
    assert session.pending_user_choice is None
    assert not session.terminal.pending_prompt_default

    run_action_tool_turn(
        "can you do a demo", session, console, is_tty=True, llm_factory=lambda: llm
    )

    pending = session.pending_user_choice
    assert pending is not None, "A later demo request must reopen the abandoned startup menu"
    assert pending.title == _TITLE
    assert _take_prompt(session) == "/choose"
    assert llm.invocations == 1
    assert session.questions_already_answered == {"another question?"}
    assert session.skills_already_prompted == {"another-skill", ONBOARDING_SKILL_NAME}


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


def test_boot_paints_only_the_skill_menu_then_selected_child_runs_through_real_turns(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    """Boot output contract: the skill's entry menu is the first paint and needs no model."""
    _offerable(monkeypatch)
    # GitHub is ready, so the demo starts without the setup menu.
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_ready")
    session = Session()
    session.resolved_integrations_cache = {}
    buffer = io.StringIO()
    console = Console(file=buffer, highlight=False)
    # The pick enters the chosen skill, so its first response is already step 1.
    llm = FakeActionLLM(
        [
            tool_response("scan_local_git_workspace"),
            tool_response(
                "ask_user_choice",
                {"title": _REPOSITORY_TITLE, "options": list(_REPOSITORY_OPTIONS)},
            ),
        ]
    )
    scans: list[str] = []
    picker_calls: list[dict[str, Any]] = []

    def scan(root: Any, **_kwargs: Any) -> WorkspaceSnapshot:
        scans.append(str(root))
        return WorkspaceSnapshot(root=str(root), days=30, repos=())

    def pick(**kwargs: Any) -> str:
        picker_calls.append(kwargs)
        return _REPOSITORY if kwargs["title"] == _REPOSITORY_TITLE else ANALYZE_REPO_OPTION

    monkeypatch.setattr(scan_tool, "scan_workspace", scan)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    assert demo_picker.offer_demo(session, console)
    # The host asked the skill's question itself: no prose prompt, no model, no output.
    assert buffer.getvalue() == ""
    assert session.active_skill == ONBOARDING_SKILL_NAME
    pending = session.pending_user_choice
    assert pending is not None
    assert (pending.title, pending.note, pending.options) == (
        _TITLE,
        _NOTE,
        OUTCOME_MENU_OPTIONS,
    )
    assert session.terminal.pending_prompt_default == "/choose"
    assert session.terminal.awaiting_handoff_answer

    # The controller reserves stdin for literal /choose before dispatching the turn.
    session.terminal.exclusive_stdin_active = True
    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: llm
    )
    session.terminal.exclusive_stdin_active = False
    assert llm.invocations == 0  # Nothing before the pick used the model.
    # The analytics skill leaves repository selection to the model's next turn.
    painted = buffer.getvalue()
    assert _TITLE in painted
    assert _REPOSITORY_TITLE not in painted
    for chrome in ("/goal", "Skill ", "activated", "skill_view", "[1]"):
        assert chrome not in painted, painted
    assert len(picker_calls) == 1
    on_custom_answer = picker_calls[0].pop("on_custom_answer")
    on_answer = picker_calls[0].pop("on_answer")
    on_dismiss = picker_calls[0].pop("on_dismiss")
    assert callable(on_custom_answer)
    assert callable(on_answer)
    assert callable(on_dismiss)
    assert picker_calls[0] == {
        "title": _TITLE,
        "choices": [(option, option) for option in OUTCOME_MENU_OPTIONS],
        "custom_label": None,
        "multi_select": False,
        "header": "Ask User",
        "letter_keys": True,
        "note": _NOTE,
    }
    # The pick entered the chosen skill: its answer turn carries that skill's
    # body, not the onboarding router's, and needs no skill_view round trip.
    assert session.active_skill == "analyzing-github-ci-performance"
    answer = _take_prompt(session)
    assert answer == format_ask_user_answers(pending.items(), (ANALYZE_REPO_OPTION,))
    envelope = build_action_system_prompt_envelope(
        TurnSnapshot.from_session(answer, session, surface="interactive_shell")
    )
    assert "ACTIVE SKILL: analyzing-github-ci-performance" in envelope.render_ephemeral()
    assert "## Follow the selected child" not in envelope.render_ephemeral()
    assert "ACTIVE SKILL:" not in envelope.render_cached()

    run_action_tool_turn(answer, session, console, is_tty=True, llm_factory=lambda: llm)
    assert len(scans) == 1
    assert session.active_skill == "analyzing-github-ci-performance"
    assert session.pending_user_choice is not None, buffer.getvalue()
    assert session.pending_user_choice.title == _REPOSITORY_TITLE
    assert session.pending_user_choice.options == _REPOSITORY_OPTIONS
    assert llm.invocations == 2
    # Raw-data analysis retains the full catalog for model-selected collection.
    assert onboarding_outcomes == [("ci_analytics", False)]

    # An explicit new demo may ask the child's repository question again,
    # while unrelated answered questions remain settled.
    choice_prompt._cmd_choose(session, console, [])
    _take_prompt(session)
    session.questions_already_answered.add("deploy to production?")
    assert demo_picker.offer_demo(session, console, force=True)
    _take_prompt(session)
    choice_prompt._cmd_choose(session, console, [])
    replay_answer = _take_prompt(session)
    replay_llm = FakeActionLLM(
        [
            tool_response("scan_local_git_workspace"),
            tool_response(
                "ask_user_choice",
                {"title": _REPOSITORY_TITLE, "options": list(_REPOSITORY_OPTIONS)},
            ),
        ]
    )
    run_action_tool_turn(
        replay_answer, session, console, is_tty=True, llm_factory=lambda: replay_llm
    )
    assert len(scans) == 2
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == _REPOSITORY_TITLE
    assert "deploy to production?" in session.questions_already_answered


def test_a_demo_entered_at_the_pick_is_nudged_past_a_reply_that_runs_nothing(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    """Without a skill load to catch, a no-work reply on the pick's turn still stalls."""
    del onboarding_outcomes
    _offerable(monkeypatch)
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_ready")
    session = Session()
    session.resolved_integrations_cache = {}
    console = Console(file=io.StringIO(), highlight=False)
    scans: list[str] = []

    def scan(root: Any, **_kwargs: Any) -> WorkspaceSnapshot:
        scans.append(str(root))
        return WorkspaceSnapshot(root=str(root), days=30, repos=())

    monkeypatch.setattr(scan_tool, "scan_workspace", scan)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: ANALYZE_REPO_OPTION)
    assert demo_picker.offer_demo(session, console)
    session.terminal.exclusive_stdin_active = True
    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: FakeActionLLM([])
    )
    session.terminal.exclusive_stdin_active = False
    llm = FakeActionLLM(
        [
            no_tool_response("I'll scan your repositories next."),
            tool_response("scan_local_git_workspace"),
            tool_response(
                "ask_user_choice",
                {"title": _REPOSITORY_TITLE, "options": list(_REPOSITORY_OPTIONS)},
            ),
        ]
    )

    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: llm
    )

    assert len(scans) == 1
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == _REPOSITORY_TITLE
    assert llm.invocations == 3


def test_without_github_the_demo_opens_setup_first_and_resumes_after_it(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
    tmp_path: Path,
) -> None:
    """No token: setup comes before any scan or repository question, then the demo resumes.

    The demo used to scan, ask which repository to analyze, and only then fail
    on the missing token; finishing setup never brought the user back.
    """
    # Arrange: no GitHub credential anywhere; the local store lives in tmp_path.
    _offerable(monkeypatch)
    _no_github_token(monkeypatch)
    monkeypatch.setenv(INTEGRATIONS_STORE_PATH_ENV, str(tmp_path / "integrations.json"))
    assert resolve_store_path().is_relative_to(tmp_path)
    session = Session()
    session.resolved_integrations_cache = {}
    buffer = io.StringIO()
    console = Console(file=buffer, highlight=False)
    load_demo = tool_response("skill_view", {"name": "analyzing-github-ci-performance"})
    llm = FakeActionLLM(
        [
            load_demo,
            load_demo,
            tool_response("scan_local_git_workspace"),
            tool_response(
                "ask_user_choice",
                {"title": _REPOSITORY_TITLE, "options": list(_REPOSITORY_OPTIONS)},
            ),
        ]
    )
    scans: list[str] = []
    titles: list[str] = []
    picks = iter([ANALYZE_REPO_OPTION, "Set up GitHub on this machine"])

    def scan(root: Any, **_kwargs: Any) -> WorkspaceSnapshot:
        scans.append(str(root))
        return WorkspaceSnapshot(root=str(root), days=30, repos=())

    def pick(**kwargs: Any) -> str:
        titles.append(kwargs["title"])
        return next(picks)

    def local_wizard(_console: Console, args: list[str], **_kwargs: Any) -> bool:
        assert args == ["integrations", "setup", "github"]
        upsert_integration("github", {"credentials": {"auth_token": "ghp_local"}})
        return True

    monkeypatch.setattr(scan_tool, "scan_workspace", scan)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    monkeypatch.setattr(integrations_cmds, "run_cli_command", local_wizard)

    # Act 1: startup, then the user picks the analysis demo.
    assert demo_picker.offer_demo(session, console)
    _run_slash_turn(session, console, _take_prompt(session))
    answer = _take_prompt(session)
    run_action_tool_turn(answer, session, console, is_tty=True, llm_factory=lambda: llm)

    # Assert: the setup menu is queued before any scan or repository question.
    assert llm.invocations == 1
    assert scans == []
    pending = session.pending_user_choice
    assert pending is not None
    assert pending.title == "Connect GitHub to continue"
    assert _REPOSITORY_TITLE not in buffer.getvalue()
    assert session.active_skill == ONBOARDING_SKILL_NAME

    # Act 2: the user sets GitHub up on this machine; the wizard saves a token.
    _run_slash_turn(session, console, _take_prompt(session))
    _run_slash_turn(session, console, _take_prompt(session))

    # Assert: the menu answer is resubmitted exactly as it was first sent.
    replay = _take_prompt(session)
    assert replay == answer
    assert session.terminal.awaiting_handoff_answer
    assert pending_setup_resume(session) is None

    # Act 3: the resubmitted answer runs the demo from its first step.
    run_action_tool_turn(replay, session, console, is_tty=True, llm_factory=lambda: llm)

    # Assert: the demo reached the repository question.
    assert len(scans) == 1
    assert session.active_skill == "analyzing-github-ci-performance"
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == _REPOSITORY_TITLE
    assert llm.invocations == 4
    assert titles == [_TITLE, "Connect GitHub to continue"]
    assert onboarding_outcomes == [("ci_analytics", False)]


def test_declining_github_setup_ends_the_demo_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offerable(monkeypatch)
    _no_github_token(monkeypatch)
    session = Session()
    session.resolved_integrations_cache = {}
    buffer = io.StringIO()
    console = Console(file=buffer, highlight=False)
    llm = FakeActionLLM([tool_response("skill_view", {"name": "analyzing-github-ci-performance"})])
    picks = iter([ANALYZE_REPO_OPTION, "Not now"])

    def pick(**_kwargs: Any) -> str:
        return next(picks)

    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    assert demo_picker.offer_demo(session, console)
    _run_slash_turn(session, console, _take_prompt(session))
    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: llm
    )

    _run_slash_turn(session, console, _take_prompt(session))

    assert "Skipped GitHub setup" in buffer.getvalue()
    assert "error" not in buffer.getvalue().lower()
    assert session.active_skill is None
    assert session.pending_user_choice is None
    assert not session.terminal.pending_prompt_default
    assert pending_setup_resume(session) is None
    assert llm.invocations == 1


@pytest.mark.parametrize("answer", [None, "Inspect the deployment logs", "/help"])
def test_onboarding_cancel_custom_and_slash_do_not_reopen_the_menu(
    monkeypatch: pytest.MonkeyPatch,
    answer: str | None,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.pending_user_choice = PendingUserChoice(title=_TITLE, options=getting_started_options())
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
    for option in getting_started_options():
        answer = option
        session.active_skill = ONBOARDING_SKILL_NAME
        session.pending_user_choice = PendingUserChoice(
            title=_TITLE, options=getting_started_options()
        )
        choice_prompt._cmd_choose(session, console, [])

    session.active_skill = "analyzing-github-ci-performance"
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
    pending = PendingUserChoice(title=_TITLE, options=getting_started_options())
    session.pending_user_choice = pending

    def fail_capture(**_kwargs: Any) -> None:
        raise RuntimeError("Telemetry unavailable")

    answer = getting_started_options()[0]
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
    pending = PendingUserChoice(title=_TITLE, options=getting_started_options())
    session.pending_user_choice = pending
    answer = getting_started_options()[0]

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


def test_automation_group_submits_the_follow_up_leaf_not_the_group(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    """The automation row opens a second picker, then demo-repository permission.

    The transcript records all three questions. The model receives the leaf and
    the permission, not the group row.
    """
    _offerable(monkeypatch)
    # GitHub is ready, so the local repair demo needs no setup first.
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_ready")
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    pending = PendingUserChoice(title=_TITLE, options=OUTCOME_MENU_OPTIONS)
    session.pending_user_choice = pending
    calls: list[dict[str, Any]] = []
    create = "Create acme/opensre-ci-repair-demo-ab12"
    monkeypatch.setattr(choice_prompt, "_demo_create_option", lambda: create)

    def pick(**kwargs: Any) -> str:
        calls.append(kwargs)
        if kwargs["title"] == DEMO_REPO_PERMISSION_TITLE:
            return create
        if kwargs["title"] == AUTOMATION_MENU_TITLE:
            return LOCAL_REPAIR_OPTION
        return AUTOMATION_GROUP_OPTION

    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    output = io.StringIO()
    choice_prompt._cmd_choose(
        session, Console(file=output, force_terminal=False, highlight=False, width=100), []
    )

    assert [call["title"] for call in calls] == [
        _TITLE,
        AUTOMATION_MENU_TITLE,
        DEMO_REPO_PERMISSION_TITLE,
    ]
    assert calls[1]["choices"] == [(option, option) for option in AUTOMATION_MENU_OPTIONS]
    assert calls[2]["choices"] == [
        (create, create),
        (DEMO_REPO_DECLINE_OPTION, DEMO_REPO_DECLINE_OPTION),
    ]
    permission = AskUserQuestion(
        label="Demo repository", title=DEMO_REPO_PERMISSION_TITLE, options=(create,)
    )
    answer = _take_prompt(session)
    assert answer == format_ask_user_answers(
        (pending.items()[0], permission), (LOCAL_REPAIR_OPTION, create)
    )
    assert AUTOMATION_GROUP_OPTION not in answer
    assert onboarding_outcomes == [("ci_agent", False)]
    # The menus are erased, so the card is the only record of what was asked.
    assert [line.rstrip() for line in output.getvalue().splitlines()] == [
        "",
        "Ask User",
        "",
        f"  1.  {_TITLE}",
        f"      {AUTOMATION_GROUP_OPTION}",
        "",
        f"  2.  {AUTOMATION_MENU_TITLE}",
        f"      {LOCAL_REPAIR_OPTION}",
        "",
        f"  3.  {DEMO_REPO_PERMISSION_TITLE}",
        f"      {create}",
    ]


@pytest.mark.parametrize("leaf", [LOCAL_REPAIR_OPTION, CLOUD_REPAIR_OPTION])
@pytest.mark.parametrize("create", [True, False])
def test_a_repair_pick_with_permission_paints_one_ask_user_card(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
    leaf: str,
    create: bool,
) -> None:
    """``/choose`` paints the recap; submitting that same answer must not paint another."""
    del onboarding_outcomes
    _offerable(monkeypatch)
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_ready")
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.pending_user_choice = PendingUserChoice(title=_TITLE, options=OUTCOME_MENU_OPTIONS)
    create_option = "Create acme/opensre-ci-repair-demo-ab12"
    monkeypatch.setattr(choice_prompt, "_demo_create_option", lambda: create_option)
    picks = {
        _TITLE: AUTOMATION_GROUP_OPTION,
        AUTOMATION_MENU_TITLE: leaf,
        DEMO_REPO_PERMISSION_TITLE: create_option if create else DEMO_REPO_DECLINE_OPTION,
    }
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **kwargs: picks[kwargs["title"]])
    output = io.StringIO()
    console = Console(file=output, force_terminal=False, highlight=False, width=120)

    choice_prompt._cmd_choose(session, console, [])
    answer = _take_prompt(session)
    # What the prompt loop does with the queued answer.
    session.terminal.last_input_autosubmitted = True
    render_submitted_prompt(console, session, answer)

    lines = [line.strip() for line in output.getvalue().splitlines()]
    assert lines.count("Ask User") == 1
    assert session.terminal.handoff_recap_text is None


def test_a_typed_ask_user_answer_still_paints_its_card(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only the exact answer ``/choose`` already recapped skips its card."""
    del monkeypatch
    session = Session()
    session.terminal.awaiting_handoff_answer = True
    session.terminal.handoff_recap_text = "an earlier, replaced answer"
    pending = PendingUserChoice(title=_TITLE, options=OUTCOME_MENU_OPTIONS)
    permission = AskUserQuestion(
        label="Demo repository", title=DEMO_REPO_PERMISSION_TITLE, options=("Create demo",)
    )
    answer = format_ask_user_answers(
        (pending.items()[0], permission), (LOCAL_REPAIR_OPTION, "Create demo")
    )
    output = io.StringIO()

    render_submitted_prompt(
        Console(file=output, force_terminal=False, highlight=False, width=120), session, answer
    )

    assert [line.strip() for line in output.getvalue().splitlines()].count("Ask User") == 1


def test_without_github_the_local_repair_demo_asks_for_setup_before_its_repository(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    """GitHub setup comes before the demo-repository question, not after it.

    The question names a repository in the user's GitHub account, and the demo
    behind it cannot start without a token; the leaf answer is parked so the
    demo resumes once GitHub is connected.
    """
    _offerable(monkeypatch)
    _no_github_token(monkeypatch)
    session = Session()
    session.resolved_integrations_cache = {}
    session.active_skill = ONBOARDING_SKILL_NAME
    pending = PendingUserChoice(title=_TITLE, options=OUTCOME_MENU_OPTIONS)
    session.pending_user_choice = pending
    titles: list[str] = []

    def no_repository_question() -> str:
        raise AssertionError("the demo-repository question must wait for GitHub setup")

    def pick(**kwargs: Any) -> str:
        titles.append(kwargs["title"])
        if kwargs["title"] == "Connect GitHub to continue":
            return "Set up GitHub on this machine"
        if kwargs["title"] == AUTOMATION_MENU_TITLE:
            return LOCAL_REPAIR_OPTION
        return AUTOMATION_GROUP_OPTION

    monkeypatch.setattr(choice_prompt, "_demo_create_option", no_repository_question)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)

    choice_prompt._cmd_choose(session, Console(file=io.StringIO()), [])

    assert titles == [_TITLE, AUTOMATION_MENU_TITLE, "Connect GitHub to continue"]
    assert _take_prompt(session) == "/integrations setup github"
    parked = pending_setup_resume(session)
    assert parked is not None
    assert parked.text == format_ask_user_answers(pending.items(), (LOCAL_REPAIR_OPTION,))
    assert parked.skill == "scheduling-github-ci-repairs"
    assert onboarding_outcomes == [("ci_agent", False)]


@pytest.mark.parametrize(
    ("leaf", "child_skill"),
    [
        (CLOUD_REPAIR_OPTION, DELEGATING_GITHUB_CI_REPAIRS_SKILL_NAME),
        (SLACK_OPTION, CONNECTING_SLACK_SKILL_NAME),
    ],
    ids=["cloud-repair", "slack"],
)
def test_automation_picker_leaf_hands_off_to_the_current_child(
    monkeypatch: pytest.MonkeyPatch,
    leaf: str,
    child_skill: str,
) -> None:
    """The real picker submits each leaf and any demo-repository decision."""
    _offerable(monkeypatch)
    session = Session()
    session.resolved_integrations_cache = dict(_SLACK_CONNECTED)
    console = Console(file=io.StringIO(), highlight=False)
    llm = FakeActionLLM([tool_response("skill_view", {"name": child_skill})])
    picked = [AUTOMATION_GROUP_OPTION, leaf]
    if leaf == CLOUD_REPAIR_OPTION:
        picked.append(DEMO_REPO_DECLINE_OPTION)
    selections = iter(picked)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", lambda **_kw: next(selections))

    assert demo_picker.offer_demo(session, console)
    pending = session.pending_user_choice
    assert pending is not None
    session.terminal.exclusive_stdin_active = True
    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: llm
    )
    session.terminal.exclusive_stdin_active = False

    answer = _take_prompt(session)
    expected_questions = pending.items()
    expected_answers: tuple[str, ...] = (leaf,)
    if leaf == CLOUD_REPAIR_OPTION:
        permission = AskUserQuestion(
            label="Demo repository",
            title=DEMO_REPO_PERMISSION_TITLE,
            options=(DEMO_REPO_DECLINE_OPTION,),
        )
        expected_questions = (pending.items()[0], permission)
        expected_answers = (leaf, DEMO_REPO_DECLINE_OPTION)
    assert answer == format_ask_user_answers(expected_questions, expected_answers)
    assert AUTOMATION_GROUP_OPTION not in answer
    # The pick entered the child, so its answer turn starts in the child's body.
    assert session.active_skill == child_skill
    envelope = build_action_system_prompt_envelope(
        TurnSnapshot.from_session(answer, session, surface="interactive_shell")
    )
    assert f"ACTIVE SKILL: {child_skill}" in envelope.render_ephemeral()

    run_action_tool_turn(answer, session, console, is_tty=True, llm_factory=lambda: llm)

    assert session.active_skill == child_skill
    assert not llm.responses


def test_automation_follow_up_escape_cancels_without_an_answer(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.pending_user_choice = PendingUserChoice(title=_TITLE, options=OUTCOME_MENU_OPTIONS)

    def pick(**kwargs: Any) -> str | None:
        if kwargs["title"] == AUTOMATION_MENU_TITLE:
            return None
        return AUTOMATION_GROUP_OPTION

    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    choice_prompt._cmd_choose(session, Console(file=io.StringIO()), [])

    assert session.active_skill is None
    assert session.terminal.pending_prompt_default in (None, "")
    assert onboarding_outcomes == [("skipped", None)]


def test_cloud_repair_can_decline_the_demo_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    pending = PendingUserChoice(title=_TITLE, options=OUTCOME_MENU_OPTIONS)
    session.pending_user_choice = pending
    monkeypatch.setattr(
        choice_prompt, "_demo_create_option", lambda: "Create opensre-ci-repair-demo-zz99"
    )

    def pick(**kwargs: Any) -> str:
        if kwargs["title"] == DEMO_REPO_PERMISSION_TITLE:
            return DEMO_REPO_DECLINE_OPTION
        if kwargs["title"] == AUTOMATION_MENU_TITLE:
            return CLOUD_REPAIR_OPTION
        return AUTOMATION_GROUP_OPTION

    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    choice_prompt._cmd_choose(session, Console(file=io.StringIO()), [])

    answer = _take_prompt(session)
    assert CLOUD_REPAIR_OPTION in answer
    assert DEMO_REPO_DECLINE_OPTION in answer
    assert DEMO_REPO_PERMISSION_TITLE in answer


def test_slack_does_not_ask_to_create_a_demo_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.resolved_integrations_cache = dict(_SLACK_CONNECTED)
    session.active_skill = ONBOARDING_SKILL_NAME
    pending = PendingUserChoice(title=_TITLE, options=OUTCOME_MENU_OPTIONS)
    session.pending_user_choice = pending
    titles: list[str] = []

    def pick(**kwargs: Any) -> str:
        titles.append(kwargs["title"])
        if kwargs["title"] == AUTOMATION_MENU_TITLE:
            return SLACK_OPTION
        return AUTOMATION_GROUP_OPTION

    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    choice_prompt._cmd_choose(session, Console(file=io.StringIO()), [])

    assert titles == [_TITLE, AUTOMATION_MENU_TITLE]
    answer = _take_prompt(session)
    assert answer == format_ask_user_answers(pending.items(), (SLACK_OPTION,))
    assert DEMO_REPO_PERMISSION_TITLE not in answer


def test_without_slack_the_slack_demo_connects_it_in_the_app_then_resumes(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    """Connect Slack opens the organization's app home, then picks up the parked answer.

    The app stores its Slack install as ``slack_bot`` with a bot token alone;
    once that record reaches this machine the Slack check passes and the leaf
    answer is resubmitted exactly as it was first sent.
    """
    _offerable(monkeypatch)
    # A Tracer JWT (set in CI) resolves integrations from Tracer, never the app.
    for name in (
        SLACK_BOT_TOKEN_ENV,
        SLACK_APP_TOKEN_ENV,
        SLACK_WEBHOOK_URL_ENV,
        TRACER_JWT_TOKEN_ENV,
    ):
        monkeypatch.delenv(name, raising=False)
    app_records: list[dict[str, Any]] = []
    account = AccountRecord(
        user_id="user-1",
        organization_id="org_3K6",
        email=None,
        app_url="https://app.test",
        signed_in_at="2026-01-01T00:00:00Z",
        token_expires_at="2027-01-01T00:00:00Z",
    )
    for module in (account_integrations, gate):
        monkeypatch.setattr(module, "load_account_record", lambda: account)
        monkeypatch.setattr(module, "resolve_account_token", lambda: "osre_pat_test")

    def app_get(
        url: str,
        *,
        headers: dict[str, str],
        timeout: float,
        params: dict[str, str],
    ) -> httpx.Response:
        _ = (url, headers, timeout)
        assert params == {"include": "personal"}
        return httpx.Response(200, json={"success": True, "data": app_records})

    monkeypatch.setattr(
        account_integrations, "httpx", SimpleNamespace(get=app_get, HTTPError=httpx.HTTPError)
    )
    account_integrations.reset_account_integrations_cache()
    opened: list[str] = []

    def browser_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(prerequisite_menu.webbrowser, "open", browser_open)
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    pending = PendingUserChoice(title=_TITLE, options=OUTCOME_MENU_OPTIONS)
    session.pending_user_choice = pending
    titles: list[str] = []
    setup_rows: list[list[str]] = []

    def pick(**kwargs: Any) -> str:
        titles.append(kwargs["title"])
        if kwargs["title"] == _SLACK_SETUP_TITLE:
            setup_rows.append([label for label, *_rest in kwargs["choices"]])
            if not opened:
                return _SLACK_OPEN_APP
            if len(setup_rows) > 2:
                return "Not now"  # Slack never resolved: stop instead of looping
            # Connected in the browser while the menu was open.
            app_records.append(
                {
                    "id": "slack-org",
                    "service": "slack_bot",
                    "status": "active",
                    "name": "default",
                    "credentials": {"bot_token": "xoxe.xoxb-app-install"},
                }
            )
            return _SLACK_CONTINUE
        if kwargs["title"] == AUTOMATION_MENU_TITLE:
            return SLACK_OPTION
        return AUTOMATION_GROUP_OPTION

    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    buffer = io.StringIO()

    choice_prompt._cmd_choose(session, Console(file=buffer, width=200), [])

    assert titles == [
        _TITLE,
        AUTOMATION_MENU_TITLE,
        _SLACK_SETUP_TITLE,
        _SLACK_SETUP_TITLE,
    ]
    assert setup_rows[0][0] == _SLACK_OPEN_APP
    assert opened == ["https://app.test/home?org_id=org_3K6"]
    assert "https://app.test/home?org_id=org_3K6" in buffer.getvalue()
    assert _take_prompt(session) == format_ask_user_answers(pending.items(), (SLACK_OPTION,))
    assert session.terminal.awaiting_handoff_answer
    assert pending_setup_resume(session) is None
    slack = session.resolved_integrations_cache["slack"]
    assert slack["bot_token"] == "xoxe.xoxb-app-install"
    assert onboarding_outcomes == [("slack", False)]
    account_integrations.reset_account_integrations_cache()


def test_demo_repository_escape_cancels_without_an_answer(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
) -> None:
    _offerable(monkeypatch)
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.pending_user_choice = PendingUserChoice(title=_TITLE, options=OUTCOME_MENU_OPTIONS)

    def pick(**kwargs: Any) -> str | None:
        if kwargs["title"] == DEMO_REPO_PERMISSION_TITLE:
            return None
        if kwargs["title"] == AUTOMATION_MENU_TITLE:
            return CLOUD_REPAIR_OPTION
        return AUTOMATION_GROUP_OPTION

    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    choice_prompt._cmd_choose(session, Console(file=io.StringIO()), [])

    assert session.active_skill is None
    assert session.terminal.pending_prompt_default in (None, "")
    assert onboarding_outcomes == [("skipped", None)]


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
    assert session.active_skill is None
    monkeypatch.setattr(demo_picker, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(demo_picker, "is_test_run", lambda: True)
    assert not demo_picker.offer_demo(session)
    assert demo_picker.offer_demo(session, force=True)
    assert session.active_skill == ONBOARDING_SKILL_NAME
    assert session.pending_user_choice is not None
    assert _take_prompt(session) == "/choose"


def test_model_load_of_the_master_skill_opens_the_menu_and_ends_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An explicit demo request loads the skill and lets its hook open the menu."""
    _offerable(monkeypatch)
    session = Session()
    session.resolved_integrations_cache = {}
    console = Console(file=io.StringIO(), highlight=False)
    llm = FakeActionLLM([tool_response("skill_view", {"name": ONBOARDING_SKILL_NAME})])

    run_action_tool_turn("Show me a demo", session, console, is_tty=True, llm_factory=lambda: llm)

    assert llm.invocations == 1
    assert session.active_skill == ONBOARDING_SKILL_NAME
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == _TITLE
    assert session.terminal.pending_prompt_default == "/choose"


def test_startup_without_a_menu_hook_does_not_fall_back_to_a_model_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A master skill that queues no menu is a skill bug, not a reason to autosubmit prose."""
    _offerable(monkeypatch)
    session = Session()

    def enter_without_menu(_name: str, _ctx: Any) -> dict[str, Any]:
        return {"ok": True, "name": ONBOARDING_SKILL_NAME, "content": "body", "entry_menu": None}

    monkeypatch.setattr(demo_picker, "enter_skill", enter_without_menu)
    assert not demo_picker.offer_demo(session, force=True)
    assert session.terminal.pending_prompt_default is None
    assert session.active_skill is None


def test_onboarding_losing_its_terminal_ends_without_a_text_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _offerable(monkeypatch)
    session = Session()
    output = io.StringIO()
    console = Console(file=output)
    assert demo_picker.offer_demo(session, console)
    _take_prompt(session)
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: False)

    choice_prompt._cmd_choose(session, console, [])

    assert "request a task directly" in output.getvalue()
    assert all(option not in output.getvalue() for option in OUTCOME_MENU_OPTIONS)
    assert session.active_skill is None
    assert session.pending_user_choice is None
    assert not session.terminal.awaiting_handoff_answer
    assert not session.terminal.pending_prompt_default


def test_a_merge_in_progress_here_skips_the_demo_unless_forced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    _offerable(monkeypatch)
    monkeypatch.setattr(demo_picker, "merge_in_progress", lambda _cwd: True)
    session = Session()

    # Act / Assert: startup stays out of the way; /demo still opens the menu.
    assert not demo_picker.offer_demo(session)
    assert session.active_skill is None
    assert demo_picker.offer_demo(session, force=True)
    assert session.active_skill == ONBOARDING_SKILL_NAME


def test_the_analysis_demo_reads_while_its_menus_are_answered(
    monkeypatch: pytest.MonkeyPatch,
    onboarding_outcomes: list[tuple[str, bool | None]],
    tmp_path: Path,
) -> None:
    """The scan starts with the menu and the analysis at the repository pick; each runs once."""
    del onboarding_outcomes
    # Arrange: GitHub is ready, and both slow reads are faked and counted.
    _offerable(monkeypatch)
    monkeypatch.setattr(analysis_prefetch, "is_test_run", lambda: False)
    _no_github_token(monkeypatch)
    monkeypatch.setenv(GITHUB_TOKEN_ENV, "ghp_ready")
    monkeypatch.setattr(ci_tool, "snapshot_root", lambda _root=None: tmp_path)
    session = Session()
    session.resolved_integrations_cache = {}
    console = Console(file=io.StringIO(), highlight=False)
    scans: list[str] = []
    reads: list[str] = []

    # Each read records the thread it ran on: a prefetch's, or the tool call's.
    def scan(root: Any, **_kwargs: Any) -> WorkspaceSnapshot:
        scans.append(threading.current_thread().name)
        return WorkspaceSnapshot(root=str(root), days=30, repos=())

    def collect(_client: Any, *, owner: str, repo: str, **_kwargs: Any) -> CollectedRuns:
        reads.append(f"{owner}/{repo} on {threading.current_thread().name}")
        return CollectedRuns(
            default_branch="main", branch_runs=[], pr_runs=[], merged_prs=(), coverage_notices=[]
        )

    def pick(**kwargs: Any) -> str:
        return _REPOSITORY if kwargs["title"] == _REPOSITORY_TITLE else ANALYZE_REPO_OPTION

    monkeypatch.setattr(scan_tool, "scan_workspace", scan)
    monkeypatch.setattr(ci_analysis, "collect_runs", collect)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", pick)
    owner, repo = _REPOSITORY.split("/")
    llm = FakeActionLLM(
        [
            tool_response("scan_local_git_workspace"),
            tool_response(
                "ask_user_choice",
                {"title": _REPOSITORY_TITLE, "options": list(_REPOSITORY_OPTIONS)},
            ),
            tool_response(
                "analyze_github_ci_reliability", {"owner": owner, "repo": repo, "days": 30}
            ),
            no_tool_response("Here is the report."),
        ]
    )

    # Act: the menu, the demo pick, the model's scan, the repository pick, its analysis.
    assert demo_picker.offer_demo(session, console)
    _run_slash_turn(session, console, _take_prompt(session))
    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: llm
    )
    _run_slash_turn(session, console, _take_prompt(session))
    run_action_tool_turn(
        _take_prompt(session), session, console, is_tty=True, llm_factory=lambda: llm
    )

    # Assert: the tool calls took the reads started at the menus instead of repeating them.
    assert session.active_skill == ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
    assert scans == ["workspace-scan-prefetch"]
    assert reads == [f"{_REPOSITORY} on github-ci-analysis-prefetch"]
    assert llm.invocations == 4
