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
    (tmp_path / "malformed_skill.md").write_text(
        "---\n"
        "name: malformed-skill\n"
        "description: declares a menu the tool schema rejects\n"
        "pre_execute:\n"
        "  - tool: ask_user_choice\n"
        "    args: {title: 'Which one?', options: 'first, second'}\n"
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


def test_hook_args_are_gated_by_the_tool_schema_like_a_model_call(hooked_skills: None) -> None:
    """Frontmatter must not get a looser contract than the model: bad args are refused, not coerced."""
    session = Session()

    result = enter_skill("malformed-skill", _scope(session))

    assert result["pre_execute"] == [
        {
            "ok": False,
            "tool": "ask_user_choice",
            "error": "ask_user_choice.options has invalid type/value.",
        }
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
