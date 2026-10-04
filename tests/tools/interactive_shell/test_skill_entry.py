"""Entering a skill opens its catalog-built entry menu through the real ``ask_user_choice`` tool.

Unit coverage of ``tools/interactive_shell/actions/skill_entry.py``. The
interactive-shell journeys that drive it (startup, ``/demo``, a model-issued
``skill_view``) live in ``tests/interactive_shell/runtime/test_demo_picker.py``.
"""

from __future__ import annotations

import io
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console

import core.agent_harness.prompts.skills as skills
import surfaces.interactive_shell.command_registry.choice_prompt as choice_prompt
import tools.interactive_shell.actions.skill_prerequisite_gate as gate
from config.account import AccountRecord
from config.constants import GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV, GITHUB_TOKEN_ENV
from config.constants.skill_prerequisites import GITHUB_REST_TOKEN_CHECK
from config.constants.skills import (
    ANALYZE_REPO_OPTION,
    ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME,
    ONBOARDING_MENU_TITLE,
    ONBOARDING_SKILL_NAME,
    SKIP_DEMO_OPTION,
)
from core.agent_harness.spi.handoff import AskUserQuestion, format_ask_user_answers
from core.agent_harness.spi.session_state import pending_setup_resume
from core.agent_harness.tools import ActionToolScope
from core.agent_harness.tools.tool_context import ACTION_TOOL_CONTEXT_RESOURCE_KEY
from core.tool import AgentToolContext
from infrastructure.harness_providers import (
    clear_skill_prerequisite_checks,
    register_skill_prerequisite_check,
)
from surfaces.interactive_shell.session import Session
from tests.utils.skill_cards import skill_card
from tools.interactive_shell.actions.ask_choice import ask_user_choice_tool
from tools.interactive_shell.actions.skill_entry import (
    MENU_QUEUED_INSTRUCTION,
    enter_skill,
    entry_menu_queued,
)
from tools.interactive_shell.actions.skill_view import execute_skill_view_tool

_GATED = ANALYZING_GITHUB_CI_PERFORMANCE_SKILL_NAME
_MASTER_ANSWER = format_ask_user_answers(
    (AskUserQuestion(label="", title=ONBOARDING_MENU_TITLE, options=()),),
    (ANALYZE_REPO_OPTION,),
)


@dataclass
class _Ports:
    tty: bool = True

    def tty_interactive(self) -> bool:
        return self.tty


def _scope(session: Session, *, tty: bool = True, turn_message: str = "") -> ActionToolScope:
    return ActionToolScope(
        session=session,
        console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
        is_tty=tty,
        slash_ports=_Ports(tty=tty),
        turn_user_message=turn_message,
    )


@pytest.fixture
def no_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (GITHUB_TOKEN_ENV, GH_TOKEN_ENV, GITHUB_MCP_AUTH_TOKEN_ENV):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture
def analytics(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, dict[str, str]]]:
    events: list[tuple[str, dict[str, str]]] = []

    def missing(**properties: str) -> None:
        events.append(("skill_prerequisite_missing", properties))

    def executed(**properties: str) -> None:
        events.append(("skill_executed", properties))

    monkeypatch.setattr(gate, "capture_skill_prerequisite_missing", missing)
    monkeypatch.setattr(
        "tools.interactive_shell.actions.skill_entry.capture_skill_executed", executed
    )
    return events


@pytest.fixture
def demo_catalog(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    """A master card with two demo children; only the master gets an entry menu."""
    (tmp_path / "master.md").write_text(skill_card(ONBOARDING_SKILL_NAME, "Follow the answer."))
    (tmp_path / "first.md").write_text(
        skill_card("first", getting_started="First demo", demo_order=1)
    )
    (tmp_path / "second.md").write_text(
        skill_card("second", getting_started="Second demo", demo_order=2)
    )
    monkeypatch.setattr(
        "core.agent_harness.prompts.skills.content.files.skills_dir", lambda: tmp_path
    )
    skills.clear_skills_caches()
    yield tmp_path
    skills.clear_skills_caches()


def test_entry_activates_the_skill_and_queues_its_menu_through_the_real_tool(
    demo_catalog: Path,
) -> None:
    session = Session()

    result = enter_skill(ONBOARDING_SKILL_NAME, _scope(session))

    assert result["ok"] is True
    assert session.active_skill == ONBOARDING_SKILL_NAME
    pending = session.pending_user_choice
    assert pending is not None and (pending.title, pending.options) == (
        ONBOARDING_MENU_TITLE,
        ("First demo", "Second demo", SKIP_DEMO_OPTION),
    )
    assert pending.custom_answer is False
    assert session.terminal.pending_prompt_default == "/choose"
    assert session.terminal.awaiting_handoff_answer
    assert result["entry_menu"]["menu"] == "queued"
    assert entry_menu_queued(result)
    assert result["content"].startswith("Follow the answer.")
    assert result["content"].endswith(MENU_QUEUED_INSTRUCTION)


def _host_enters_the_skill(session: Session) -> None:
    enter_skill(ONBOARDING_SKILL_NAME, _scope(session))


def _model_asks_the_user(session: Session) -> None:
    context = AgentToolContext(
        resolved_integrations={},
        resources={ACTION_TOOL_CONTEXT_RESOURCE_KEY: _scope(session)},
    )
    ask_user_choice_tool.run(title="Which branch?", options=["main", "release"], context=context)


@pytest.mark.parametrize(
    ("open_menu", "reason_code"),
    [(_host_enters_the_skill, "entry_menu"), (_model_asks_the_user, "choice")],
)
def test_a_rendered_menu_records_whether_the_host_or_the_model_opened_it(
    demo_catalog: Path,
    monkeypatch: pytest.MonkeyPatch,
    open_menu: Callable[[Session], None],
    reason_code: str,
) -> None:
    # Arrange: queue the menu, then render it through the real /choose command.
    session = Session()
    open_menu(session)
    rendered: list[dict[str, object]] = []
    monkeypatch.setattr(choice_prompt, "repl_tty_interactive", lambda: False)
    monkeypatch.setattr(
        choice_prompt,
        "capture_ask_user_prompt_rendered",
        lambda **properties: rendered.append(properties),
    )

    # Act
    choice_prompt._cmd_choose(session, Console(file=io.StringIO(), force_terminal=False), [])

    # Assert
    assert [properties["reason_code"] for properties in rendered] == [reason_code]


def test_a_child_skill_has_no_entry_menu(demo_catalog: Path) -> None:
    session = Session()

    result = enter_skill("first", _scope(session))

    assert result["ok"] is True
    assert result["entry_menu"] is None
    assert session.active_skill == "first"
    assert session.pending_user_choice is None
    assert MENU_QUEUED_INSTRUCTION not in result["content"]


@pytest.mark.usefixtures("no_github_token")
def test_a_skill_without_its_github_token_queues_setup_instead_of_starting(
    analytics: list[tuple[str, dict[str, str]]],
) -> None:
    """The demo used to start, scan, and ask for a repository before failing on the token.

    Held at entry, nothing about the session moves — no active skill, no menu
    bookkeeping, no ``skill_executed`` — so the message resubmitted after setup
    enters the skill exactly as this one would have.
    """
    # Arrange: the user answered the onboarding menu inside the master skill.
    session = Session()
    session.active_skill = ONBOARDING_SKILL_NAME
    session.questions_already_answered.add("earlier question")

    # Act
    result = execute_skill_view_tool(
        {"name": _GATED}, _scope(session, turn_message=_MASTER_ANSWER), resolved_integrations={}
    )

    # Assert: setup is queued and the skill body is withheld.
    assert result["prerequisite"] == {
        "check": GITHUB_REST_TOKEN_CHECK,
        "service": "github",
        "reason_code": "credential_missing",
        "menu": "queued",
    }
    assert skills.load_skill_body(_GATED) not in result["content"]
    assert "End the turn now" in result["content"]
    pending = session.pending_user_choice
    assert pending is not None
    assert pending.title == "Connect GitHub to continue"
    # The analysis demo can also go on without GitHub, on the user's local repositories.
    assert pending.options == (
        "Set up GitHub on this machine",
        "I've connected GitHub — continue",
        "Use my local repos instead (no GitHub needed)",
        "Not now",
    )
    assert pending.commands["Set up GitHub on this machine"] == "/integrations setup github"
    assert pending.custom_answer is False
    assert session.terminal.pending_prompt_default == "/choose"
    # Nothing a replay depends on has moved.
    assert session.active_skill == ONBOARDING_SKILL_NAME
    assert session.skills_already_prompted == set()
    assert session.questions_already_answered == {"earlier question"}
    assert analytics == [
        (
            "skill_prerequisite_missing",
            {
                "skill": _GATED,
                "check": GITHUB_REST_TOKEN_CHECK,
                "reason_code": "credential_missing",
            },
        )
    ]
    parked = pending_setup_resume(session)
    assert parked is not None
    assert (parked.text, parked.as_answer, parked.skill, parked.service) == (
        _MASTER_ANSWER,
        True,
        _GATED,
        "github",
    )


@pytest.mark.usefixtures("no_github_token")
def test_without_a_terminal_the_gate_answers_in_text_and_parks_nothing() -> None:
    session = Session()

    result = enter_skill(
        _GATED, _scope(session, tty=False, turn_message="analyze my CI"), resolved_integrations={}
    )

    assert result["prerequisite"]["menu"] == "unavailable"
    assert "opensre integrations setup github" in result["content"]
    assert skills.load_skill_body(_GATED) not in result["content"]
    assert session.pending_user_choice is None
    assert session.active_skill is None
    assert pending_setup_resume(session) is None


@pytest.mark.usefixtures("no_github_token")
def test_a_signed_in_machine_is_offered_the_opensre_app_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
    session = Session()

    enter_skill(_GATED, _scope(session, turn_message="analyze my CI"), resolved_integrations={})

    pending = session.pending_user_choice
    assert pending is not None
    assert pending.options[:2] == (
        "Connect GitHub in the OpenSRE app (recommended)",
        "Set up GitHub on this machine",
    )
    assert pending.commands[pending.options[0]] == "prerequisite:open-app:github"


def _raise(_resolved: Mapping[str, Any]) -> bool:
    raise RuntimeError("check bug")


@pytest.mark.parametrize(
    "condition", ["token", "unregistered check", "raising check"], ids=lambda c: c.replace(" ", "-")
)
@pytest.mark.usefixtures("no_github_token")
def test_the_skill_starts_when_its_check_passes_or_cannot_run(
    monkeypatch: pytest.MonkeyPatch,
    analytics: list[tuple[str, dict[str, str]]],
    condition: str,
) -> None:
    """A token lets the skill start; a wiring gap or a check bug fails open."""
    if condition == "token":
        monkeypatch.setenv(GH_TOKEN_ENV, "env-tok")
    else:
        clear_skill_prerequisite_checks()
        if condition == "raising check":
            register_skill_prerequisite_check(GITHUB_REST_TOKEN_CHECK, _raise)
    session = Session()

    result = enter_skill(
        _GATED, _scope(session, turn_message="analyze my CI"), resolved_integrations={}
    )

    assert "prerequisite" not in result
    assert session.active_skill == _GATED
    assert session.pending_user_choice is None
    assert pending_setup_resume(session) is None
    [(event, properties)] = analytics
    assert event == "skill_executed"
    assert (properties["skill_name"], properties["entrypoint"]) == (_GATED, "host")


def test_unavailable_menu_leaves_the_model_to_ask_in_text(demo_catalog: Path) -> None:
    session = Session()

    result = enter_skill(ONBOARDING_SKILL_NAME, _scope(session, tty=False))

    assert result["entry_menu"]["menu"] == "unavailable"
    assert not entry_menu_queued(result)
    assert session.pending_user_choice is None
    assert MENU_QUEUED_INSTRUCTION not in result["content"]


def test_skill_view_without_a_session_still_returns_the_body() -> None:
    class _NoContext:
        pass

    result = execute_skill_view_tool({"name": ONBOARDING_SKILL_NAME}, _NoContext())  # type: ignore[arg-type]

    assert result["ok"] is True
    assert result["entry_menu"] is None  # No scope to open the menu against; nothing is queued.


@pytest.mark.parametrize("still_active", [True, False], ids=["active", "left"])
def test_the_model_cannot_reopen_a_menu_the_session_already_answered(still_active: bool) -> None:
    """A greeting after a demo used to route back here and ask the same question.

    The managed-service branch ends immediately, so the next plain message
    re-entered this skill and its entry menu opened the demo menu a second
    and third time.
    """
    # Arrange: the host opened the menu once, as it does at startup.
    session = Session()
    first = enter_skill(ONBOARDING_SKILL_NAME, _scope(session))
    assert entry_menu_queued(first)
    pending = session.pending_user_choice
    assert pending is not None
    session.questions_already_answered.add(pending.title.casefold())
    session.pending_user_choice = None  # the user answered it
    session.terminal.pending_prompt_default = None
    if not still_active:
        session.active_skill = None

    # Act: the model routes back to the same skill later in the session.
    again = execute_skill_view_tool({"name": ONBOARDING_SKILL_NAME}, _scope(session))

    # Assert: the body still loads, but no second menu is queued.
    assert again["ok"] is True
    hook = again["entry_menu"]
    assert hook["tool"] == "ask_user_choice"
    assert hook["menu"] == "suppressed"
    assert hook["ok"] is False
    assert "No new menu was opened" in again["content"]
    assert MENU_QUEUED_INSTRUCTION not in again["content"]
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None


def test_model_reentry_of_the_active_skill_is_side_effect_free(demo_catalog: Path) -> None:
    """A redundant skill load must not reopen an entry menu."""
    session = Session()
    first = execute_skill_view_tool({"name": ONBOARDING_SKILL_NAME}, _scope(session))
    assert first["ok"] is True
    session.pending_user_choice = None  # the user answered the entry menu
    session.terminal.pending_prompt_default = None

    # Act: the model re-loads the same skill mid-flow.
    again = execute_skill_view_tool({"name": ONBOARDING_SKILL_NAME}, _scope(session))

    # Assert: the body comes back, but nothing about the session moved.
    assert again["ok"] is True
    assert again["already_active"] is True
    assert again["entry_menu"]["menu"] == "suppressed"
    assert again["content"].startswith("Follow the answer.")
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None


def test_the_host_may_reopen_the_menu_on_request() -> None:
    """``/demo`` and startup ask for the menu deliberately."""
    # Arrange
    session = Session()
    enter_skill(ONBOARDING_SKILL_NAME, _scope(session))
    session.pending_user_choice = None

    # Act
    again = enter_skill(ONBOARDING_SKILL_NAME, _scope(session))

    # Assert
    assert entry_menu_queued(again)
    assert session.pending_user_choice is not None


def test_demo_reopens_the_menu_after_the_session_answered_it() -> None:
    """``/demo`` means ask me again; the session's record must not silence it.

    The session-wide "already answered" guard refused the entry hook as well,
    so `/demo` queued nothing and the shell printed nothing at all.
    """
    # Arrange: the question was answered earlier in this session.
    session = Session()
    session.questions_already_answered = {"which demo would you like me to run? (esc to skip)"}

    # Act: the host enters the skill, as `/demo` and startup do.
    result = enter_skill(ONBOARDING_SKILL_NAME, _scope(session))

    # Assert
    assert entry_menu_queued(result)
    assert session.pending_user_choice is not None


def test_a_hook_that_queued_no_menu_does_not_count_as_prompted() -> None:
    """A refusal or an unavailable menu must not silence the skill for good.

    Recording the skill on any hook result meant one transient failure kept the
    user from ever seeing the menu again in that session.
    """
    # Arrange: no terminal facet, so the menu reports itself unavailable.
    session = Session()
    scope = _scope(session, tty=False)

    # Act
    result = enter_skill(ONBOARDING_SKILL_NAME, scope)

    # Assert: nothing opened, so nothing is remembered.
    assert not entry_menu_queued(result)
    assert session.skills_already_prompted == set()


def test_a_fresh_session_forgets_what_was_answered() -> None:
    """``/new`` means a new session; a remembered answer would suppress its menus."""
    # Arrange
    session = Session()
    enter_skill(ONBOARDING_SKILL_NAME, _scope(session))
    session.questions_already_answered.add("which demo would you like me to run? (esc to skip)")
    assert session.skill_question_keys

    # Act
    session.clear()

    # Assert
    assert session.questions_already_answered == set()
    assert session.skill_question_keys == {}
    assert session.skills_already_prompted == set()


def test_demo_menu_and_handoffs_follow_current_child_metadata(demo_catalog: Path) -> None:
    first = demo_catalog / "first.md"
    initial = Session()
    enter_skill(ONBOARDING_SKILL_NAME, _scope(initial))
    assert initial.pending_user_choice is not None
    assert initial.pending_user_choice.options[:2] == ("First demo", "Second demo")

    first.write_text(skill_card("first", getting_started="Renamed demo", demo_order=3))
    skills.clear_skills_caches()
    refreshed = Session()
    result = enter_skill(ONBOARDING_SKILL_NAME, _scope(refreshed))

    assert refreshed.pending_user_choice is not None
    assert refreshed.pending_user_choice.options[:2] == ("Second demo", "Renamed demo")
    assert '"Renamed demo": call `skill_view(name="first")`' in result["content"]
    assert "First demo" not in result["content"]
