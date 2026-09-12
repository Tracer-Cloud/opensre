"""Entering a skill runs its ``pre_execute`` hooks through the real tools, allowlisted.

Unit coverage of ``tools/interactive_shell/actions/skill_entry.py``. The
interactive-shell journeys that drive it (startup, ``/demo``, a model-issued
``skill_view``) live in ``tests/interactive_shell/runtime/test_demo_picker.py``.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from rich.console import Console

import core.agent_harness.prompts.skills.loader as loader
from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.tools import ActionToolScope
from surfaces.interactive_shell.session import Session
from tests.utils.skill_cards import skill_card
from tools.interactive_shell.actions.skill_entry import (
    MENU_QUEUED_INSTRUCTION,
    enter_skill,
    pre_execute_queued_menu,
)
from tools.interactive_shell.actions.skill_view import execute_skill_view_tool


@dataclass
class _Ports:
    tty: bool = True

    def tty_interactive(self) -> bool:
        return self.tty


def _scope(session: Session, *, tty: bool = True) -> ActionToolScope:
    return ActionToolScope(
        session=session,
        console=Console(file=io.StringIO(), force_terminal=False, highlight=False),
        is_tty=tty,
        slash_ports=_Ports(tty=tty),
    )


@pytest.fixture
def hooked_skills(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    for name, tool, args in (
        ("menu-skill", "ask_user_choice", {"title": "Which one?", "options": ["first", "second"]}),
        ("rogue-skill", "shell_run", {"command": "echo hello"}),
        ("malformed-skill", "ask_user_choice", {"title": "Which one?", "options": "first, second"}),
    ):
        (tmp_path / f"{name}.md").write_text(
            skill_card(name, "Follow the answer.", pre_execute=[{"tool": tool, "args": args}])
        )
    monkeypatch.setattr(loader, "skills_dir", lambda: tmp_path)
    loader.clear_skills_caches()
    yield
    loader.clear_skills_caches()


def test_entry_activates_the_skill_and_queues_its_menu_through_the_real_tool(
    hooked_skills: None,
) -> None:
    session = Session()

    result = enter_skill("menu-skill", _scope(session))

    assert result["ok"] is True
    assert session.active_skill == "menu-skill"
    pending = session.pending_user_choice
    assert pending is not None and (pending.title, pending.options) == (
        "Which one?",
        ("first", "second"),
    )
    assert session.terminal.pending_prompt_default == "/choose"
    assert session.terminal.awaiting_handoff_answer
    assert result["pre_execute"][0]["menu"] == "queued"
    assert result["content"].startswith("Follow the answer.")
    assert result["content"].endswith(MENU_QUEUED_INSTRUCTION)


@pytest.mark.parametrize("name", ["rogue-skill", "malformed-skill"])
def test_invalid_entry_hooks_exclude_the_card(hooked_skills: None, name: str) -> None:
    session = Session()
    result = enter_skill(name, _scope(session))
    assert result["ok"] is False
    assert session.active_skill is None
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None
    assert any(name in diagnostic for diagnostic in loader.read_skill_catalog().diagnostics)


def test_unavailable_menu_leaves_the_model_to_ask_in_text(hooked_skills: None) -> None:
    session = Session()

    result = enter_skill("menu-skill", _scope(session, tty=False))

    assert result["pre_execute"][0]["menu"] == "unavailable"
    assert session.pending_user_choice is None
    assert MENU_QUEUED_INSTRUCTION not in result["content"]


def test_skill_view_without_a_session_still_returns_the_body() -> None:
    class _NoContext:
        pass

    result = execute_skill_view_tool({"name": ONBOARDING_SKILL_NAME}, _NoContext())  # type: ignore[arg-type]

    assert result["ok"] is True
    assert result["pre_execute"] == []  # No scope to run hooks against; nothing is queued.


@pytest.mark.parametrize("still_active", [True, False], ids=["active", "left"])
def test_the_model_cannot_reopen_a_menu_the_session_already_answered(still_active: bool) -> None:
    """A greeting after a demo used to route back here and ask the same question.

    The managed-service branch ends immediately, so the next plain message
    re-entered this skill and its ``pre_execute`` opened the demo menu a second
    and third time.
    """
    # Arrange: the host opened the menu once, as it does at startup.
    session = Session()
    first = enter_skill(ONBOARDING_SKILL_NAME, _scope(session))
    assert first["pre_execute"]
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
    hook = again["pre_execute"][0]
    assert hook["tool"] == "ask_user_choice"
    assert hook["menu"] == "suppressed"
    assert hook["ok"] is False
    assert "No new menu was opened" in again["content"]
    assert MENU_QUEUED_INSTRUCTION not in again["content"]
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None


def test_model_reentry_of_the_active_skill_is_side_effect_free(hooked_skills: None) -> None:
    """A redundant skill load must not reopen an entry menu."""
    session = Session()
    first = execute_skill_view_tool({"name": "menu-skill"}, _scope(session))
    assert first["ok"] is True
    session.pending_user_choice = None  # the user answered the entry menu
    session.terminal.pending_prompt_default = None

    # Act: the model re-loads the same skill mid-flow.
    again = execute_skill_view_tool({"name": "menu-skill"}, _scope(session))

    # Assert: the body comes back, but nothing about the session moved.
    assert again["ok"] is True
    assert again["already_active"] is True
    assert again["pre_execute"][0]["menu"] == "suppressed"
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
    assert again["pre_execute"]
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
    assert result["pre_execute"]
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
    assert not pre_execute_queued_menu(result.get("pre_execute", []))
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


def test_demo_menu_and_handoffs_follow_current_child_metadata(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    master = tmp_path / "master.md"
    master.write_text(
        skill_card(
            ONBOARDING_SKILL_NAME,
            pre_execute=[{"tool": "ask_user_choice", "args": {"title": "Choose a demo"}}],
        )
    )
    first = tmp_path / "first.md"
    first.write_text(skill_card("first", getting_started="Original label", demo_order=1))
    (tmp_path / "second.md").write_text(
        skill_card("second", getting_started="Second demo", demo_order=2)
    )
    monkeypatch.setattr(loader, "skills_dir", lambda: tmp_path)
    loader.clear_skills_caches()
    try:
        initial = Session()
        enter_skill(ONBOARDING_SKILL_NAME, _scope(initial))
        assert initial.pending_user_choice is not None
        assert initial.pending_user_choice.options[:2] == ("Original label", "Second demo")

        first.write_text(skill_card("first", getting_started="Renamed demo", demo_order=3))
        loader.clear_skills_caches()
        refreshed = Session()
        result = enter_skill(ONBOARDING_SKILL_NAME, _scope(refreshed))

        assert refreshed.pending_user_choice is not None
        assert refreshed.pending_user_choice.options[:2] == ("Second demo", "Renamed demo")
        assert '"Renamed demo": call `skill_view(name="first")`' in result["content"]
        assert "Original label" not in result["content"]
    finally:
        loader.clear_skills_caches()
