"""The setup menu a skill prerequisite opens: each row through the real ``/choose`` handler.

Its rows are shell actions, never answers for the model and never an
onboarding-demo choice, even while the onboarding master skill is active.
"""

from __future__ import annotations

import io
from typing import Any

import pytest
from rich.console import Console

import surfaces.interactive_shell.command_registry.choice_prompt as choice_prompt
import surfaces.interactive_shell.command_registry.prerequisite_menu as prerequisite_menu
import tools.interactive_shell.actions.skill_prerequisite_gate as gate
from config.account import AccountRecord
from config.constants import (
    GH_TOKEN_ENV,
    GITHUB_MCP_AUTH_TOKEN_ENV,
    GITHUB_MCP_COMMAND_ENV,
    GITHUB_MCP_MODE_ENV,
    GITHUB_MCP_URL_ENV,
    GITHUB_TOKEN_ENV,
)
from config.constants.skills import (
    ANALYZE_REPO_OPTION,
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME,
    ONBOARDING_MENU_TITLE,
    ONBOARDING_SKILL_NAME,
    SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME,
)
from core.agent_harness.session.pending_choice import PendingUserChoice
from core.agent_harness.spi.handoff import AskUserQuestion, format_ask_user_answers
from core.agent_harness.spi.session_state import arm_setup_resume, pending_setup_resume
from surfaces.interactive_shell.session import Session

_APP_URL = "https://app.test/home?org_id=org-1"
_OPEN_APP = "Connect GitHub in the OpenSRE app (recommended)"
_LOCAL_SETUP = "Set up GitHub on this machine"
_CONTINUE = "I've connected GitHub — continue"
_NOT_NOW = "Not now"
_LOCAL_REPOS = "Use my local repos instead (no GitHub needed)"
_ANSWER = format_ask_user_answers(
    (AskUserQuestion(label="", title=ONBOARDING_MENU_TITLE, options=()),),
    (ANALYZE_REPO_OPTION,),
)


@pytest.fixture(autouse=True)
def _no_ambient_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    """No GitHub from the developer's shell or repo ``.env`` (loaded by the root conftest).

    An MCP URL or command alone makes the env loader synthesize a tokenless
    ``github`` integration; its connection selection fails, which withholds the
    ``GH_TOKEN`` fallback the tests set.
    """
    for name in (
        GITHUB_TOKEN_ENV,
        GH_TOKEN_ENV,
        GITHUB_MCP_AUTH_TOKEN_ENV,
        GITHUB_MCP_URL_ENV,
        GITHUB_MCP_MODE_ENV,
        GITHUB_MCP_COMMAND_ENV,
    ):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def onboarding_choices(monkeypatch: pytest.MonkeyPatch) -> list[str | None]:
    """Onboarding-demo outcomes ``/choose`` recorded; the setup menu must add none."""
    recorded: list[str | None] = []

    def capture(_skill: str | None, selected: str | None, *, custom: bool) -> None:
        _ = custom
        recorded.append(selected)

    monkeypatch.setattr(choice_prompt, "capture_onboarding_choice", capture)
    return recorded


def _signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    record = AccountRecord(
        user_id="user-1",
        organization_id="org-1",
        email=None,
        app_url="https://app.test",
        signed_in_at="2026-01-01T00:00:00Z",
        token_expires_at="2027-01-01T00:00:00Z",
    )

    def load_record() -> AccountRecord:
        return record

    monkeypatch.setattr(gate, "load_account_record", load_record)
    monkeypatch.setattr(gate, "resolve_account_token", lambda: "osre_pat_secret_value")


def _held_demo() -> Session:
    """The master menu's answer was parked when the analysis demo hit the gate."""
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.resolved_integrations_cache = {}
    assert arm_setup_resume(
        session, _ANSWER, skill=ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME, service="github"
    )
    gate.queue_prerequisite_menu(session, "github")
    session.terminal.pop_pending_prompt_default()
    session.terminal.pop_pending_autosubmit()
    return session


def _pick(monkeypatch: pytest.MonkeyPatch, *answers: str | None) -> list[dict[str, Any]]:
    """Answer successive menus with ``answers``; return what each menu showed."""
    shown: list[dict[str, Any]] = []
    queue = list(answers)

    def choose(**kwargs: Any) -> str | None:
        shown.append(kwargs)
        return queue.pop(0)

    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(choice_prompt, "repl_choose_one", choose)
    return shown


def _console() -> tuple[Console, io.StringIO]:
    buffer = io.StringIO()
    return Console(file=buffer, force_terminal=False, highlight=False, width=200), buffer


def test_continue_replays_the_parked_answer_once_github_is_connected(
    monkeypatch: pytest.MonkeyPatch, onboarding_choices: list[str | None]
) -> None:
    session = _held_demo()
    _pick(monkeypatch, _CONTINUE)
    refreshes: list[bool] = []

    def load_app_integrations(*, refresh: bool = False) -> list[dict[str, Any]]:
        refreshes.append(refresh)
        return []

    monkeypatch.setattr(prerequisite_menu, "load_account_integrations", load_app_integrations)
    monkeypatch.setenv(GH_TOKEN_ENV, "env-tok")  # connected while the menu was open
    console, _buffer = _console()

    assert choice_prompt._cmd_choose(session, console, []) is True

    assert refreshes == [True]
    assert session.terminal.pending_prompt_default == _ANSWER
    assert session.terminal.awaiting_handoff_answer is True
    assert session.active_skill == ONBOARDING_SKILL_NAME
    assert pending_setup_resume(session) is None
    assert onboarding_choices == []


def test_continue_without_a_connection_asks_again_in_the_same_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second autosubmitted ``/choose`` never paints, so the menu reopens in this one."""
    session = _held_demo()
    shown = _pick(monkeypatch, _CONTINUE, None)
    console, _buffer = _console()

    choice_prompt._cmd_choose(session, console, [])

    assert [menu["note"] for menu in shown][1] == (
        "GitHub is still not connected: no usable token was found."
    )
    assert session.terminal.pending_prompt_default is None


def test_the_app_row_opens_the_organization_home_and_asks_again(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _signed_in(monkeypatch)
    session = _held_demo()
    shown = _pick(monkeypatch, _OPEN_APP, _CONTINUE)
    opened: list[str] = []

    def browser_open(url: str) -> bool:
        opened.append(url)
        return True

    monkeypatch.setattr(prerequisite_menu, "account_setup_url", lambda: _APP_URL)
    monkeypatch.setattr(prerequisite_menu.webbrowser, "open", browser_open)
    monkeypatch.setattr(prerequisite_menu, "load_account_integrations", lambda **_kw: [])
    monkeypatch.setenv(GH_TOKEN_ENV, "app-tok")  # the app connection reached this machine
    console, buffer = _console()

    choice_prompt._cmd_choose(session, console, [])

    assert opened == [_APP_URL]
    assert _APP_URL in buffer.getvalue()
    assert [menu["choices"][0][0] for menu in shown] == [_OPEN_APP, _OPEN_APP]
    assert session.terminal.pending_prompt_default == _ANSWER


def test_not_now_ends_cleanly_and_drops_the_parked_turn(
    monkeypatch: pytest.MonkeyPatch, onboarding_choices: list[str | None]
) -> None:
    session = _held_demo()
    _pick(monkeypatch, _NOT_NOW)
    console, buffer = _console()

    assert choice_prompt._cmd_choose(session, console, []) is True

    assert "Skipped GitHub setup" in buffer.getvalue()
    assert pending_setup_resume(session) is None
    assert session.active_skill is None
    assert session.terminal.pending_prompt_default is None
    assert session.terminal.awaiting_handoff_answer is False
    assert onboarding_choices == []


def test_local_setup_runs_the_wizard_and_keeps_the_turn_parked(
    monkeypatch: pytest.MonkeyPatch, onboarding_choices: list[str | None]
) -> None:
    session = _held_demo()
    _pick(monkeypatch, _LOCAL_SETUP)
    console, _buffer = _console()

    choice_prompt._cmd_choose(session, console, [])

    assert session.terminal.pending_prompt_default == "/integrations setup github"
    assert session.terminal.awaiting_handoff_answer is False
    assert pending_setup_resume(session) is not None
    assert onboarding_choices == []


def _catalog_with(*names: str) -> Any:
    """An active catalog whose skills are exactly ``names``."""

    def find(name: str) -> object | None:
        return object() if name in names else None

    current = type("Current", (), {"find": staticmethod(find)})()
    return type("Catalog", (), {"current": staticmethod(lambda: current)})()


def test_going_on_without_github_starts_the_local_analysis_with_this_pick(
    monkeypatch: pytest.MonkeyPatch, onboarding_choices: list[str | None]
) -> None:
    """The parked demo is dropped; the local skill starts and receives the pick as its answer."""
    monkeypatch.setattr(
        gate, "active_skill_catalog", lambda: _catalog_with(ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME)
    )
    entered: list[str] = []

    def enter_skill(name: str, _ctx: Any) -> dict[str, Any]:
        entered.append(name)
        return {"ok": True, "name": name}

    monkeypatch.setattr(prerequisite_menu, "enter_skill", enter_skill)
    session = _held_demo()
    shown = _pick(monkeypatch, _LOCAL_REPOS)
    console, buffer = _console()

    assert choice_prompt._cmd_choose(session, console, []) is True

    assert [label for label, _value in shown[0]["choices"]][-2:] == [_LOCAL_REPOS, _NOT_NOW]
    assert entered == [ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME]
    assert pending_setup_resume(session) is None
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        (AskUserQuestion(label="GitHub", title="Connect GitHub to continue", options=()),),
        (_LOCAL_REPOS,),
    )
    assert session.terminal.awaiting_handoff_answer is True
    assert "Going on without GitHub" in buffer.getvalue()
    assert onboarding_choices == []


def test_a_fallback_skill_that_opens_its_own_menu_is_shown_that_menu(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A skill's own first question wins over an answer to the setup menu."""
    monkeypatch.setattr(
        gate, "active_skill_catalog", lambda: _catalog_with(ANALYZING_LOCAL_REPOSITORIES_SKILL_NAME)
    )

    def enter_skill(name: str, ctx: Any) -> dict[str, Any]:
        ctx.session.pending_user_choice = PendingUserChoice(
            title="Pick a scope", options=("A", "B")
        )
        ctx.session.terminal.set_auto_command("/choose")
        return {"ok": True, "name": name, "entry_menu": {"ok": True, "menu": "queued"}}

    monkeypatch.setattr(prerequisite_menu, "enter_skill", enter_skill)
    session = _held_demo()
    shown = _pick(monkeypatch, _LOCAL_REPOS, "B")
    console, _buffer = _console()

    choice_prompt._cmd_choose(session, console, [])

    assert [menu["title"] for menu in shown] == ["Connect GitHub to continue", "Pick a scope"]
    assert session.terminal.pending_prompt_default == format_ask_user_answers(
        (AskUserQuestion(label="", title="Pick a scope", options=()),), ("B",)
    )


def test_the_fallback_row_waits_for_its_skill_to_be_in_the_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Until the fallback skill ships in the catalog this process serves, the menu does not offer it."""
    monkeypatch.setattr(gate, "active_skill_catalog", lambda: _catalog_with())
    session = _held_demo()

    pending = gate.prerequisite_menu("github", skill=ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME)

    assert pending_setup_resume(session) is not None
    assert _LOCAL_REPOS not in pending.options
    assert pending.options[-1] == _NOT_NOW


def test_only_a_skill_with_a_fallback_offers_one() -> None:
    session = Session()
    assert arm_setup_resume(
        session, "go", skill=SCHEDULING_GITHUB_CI_REPAIRS_SKILL_NAME, service="github"
    )

    gate.queue_prerequisite_menu(session, "github")

    pending = session.pending_user_choice
    assert pending is not None
    assert _LOCAL_REPOS not in pending.options
    assert pending.options[-1] == _NOT_NOW
