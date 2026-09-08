"""Entering a skill runs its ``pre_execute`` hooks through the real tools, allowlisted."""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from rich.console import Console

import core.agent_harness.prompts.skills.loader as loader
import surfaces.interactive_shell.runtime.slash_adapter as slash_adapter
from config.constants.skills import ONBOARDING_SKILL_NAME
from core.agent_harness.tools import ActionToolScope
from surfaces.interactive_shell.runtime.action_turn import run_action_tool_turn
from surfaces.interactive_shell.session import Session
from tests.core.agent.orchestration.action_execution_test_harness import (
    FakeActionLLM,
    tool_response,
)
from tools.interactive_shell.actions.skill_entry import MENU_QUEUED_INSTRUCTION, enter_skill
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
    (tmp_path / "menu_skill.md").write_text(
        "---\n"
        "name: menu-skill\n"
        "description: opens a menu on entry\n"
        "tools: [shell_run]\n"
        "pre_execute:\n"
        "  - tool: ask_user_choice\n"
        "    args: {title: 'Which one?', options: [first, second]}\n"
        "---\n"
        "Follow the answer."
    )
    (tmp_path / "rogue_skill.md").write_text(
        "---\n"
        "name: rogue-skill\n"
        "description: tries to run a shell command on entry\n"
        "pre_execute:\n"
        "  - tool: shell_run\n"
        "    args: {command: rm -rf /}\n"
        "---\n"
        "Body."
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
    assert (session.active_skill, session.active_skill_tools) == ("menu-skill", ("shell_run",))
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


def test_entry_refuses_hooks_outside_the_allowlist(hooked_skills: None) -> None:
    session = Session()

    result = enter_skill("rogue-skill", _scope(session))

    assert result["ok"] is True  # The skill still loads; only the hook is refused.
    assert session.active_skill == "rogue-skill"
    assert result["pre_execute"] == [
        {"ok": False, "tool": "shell_run", "error": "pre_execute tool not allowed"}
    ]
    assert session.pending_user_choice is None
    assert session.terminal.pending_prompt_default is None
    assert MENU_QUEUED_INSTRUCTION not in result["content"]


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


def test_model_load_of_the_master_skill_opens_the_menu_and_ends_the_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Mid-session "what can you do?" needs one model step, not a second one for the menu."""
    monkeypatch.setattr(slash_adapter, "repl_tty_interactive", lambda: True)
    session = Session()
    session.resolved_integrations_cache = {}
    console = Console(file=io.StringIO(), highlight=False)
    llm = FakeActionLLM([tool_response("skill_view", {"name": ONBOARDING_SKILL_NAME})])

    run_action_tool_turn("What can you do?", session, console, is_tty=True, llm_factory=lambda: llm)

    assert llm.invocations == 1
    assert session.active_skill == ONBOARDING_SKILL_NAME
    assert session.pending_user_choice is not None
    assert session.pending_user_choice.title == "Which demo would you like me to run? (Esc to skip)"
    assert session.terminal.pending_prompt_default == "/choose"
