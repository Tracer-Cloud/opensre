"""Enter an action-agent skill: activate it on the session and open its entry menu.

One entry point serves every way into a skill. The model enters through the
``skill_view`` tool; the host enters directly (interactive startup, ``/demo``)
with no model step and no tool-event render. A skill's entry menu is catalog
data (``ActionSkill.entry_menu``, built by the loader), never frontmatter; it
opens here through the real ``ask_user_choice`` executor, so a host-opened menu
behaves exactly as if the model had called the tool.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.agent_harness import normalize_skill_name
from core.agent_harness.spi.grounding import (
    ActionSkill,
    SkillEntryMenu,
    list_action_skills,
    load_skill_body,
)
from core.agent_harness.spi.handoff import question_key
from core.agent_harness.tools import ActionToolScope
from infrastructure.analytics.capture import capture_skill_executed
from tools.interactive_shell.actions.ask_choice import (
    ask_user_choice_tool,
    execute_ask_user_choice_tool,
)

_ENTRY_MENU_TOOL = "ask_user_choice"

MENU_QUEUED_INSTRUCTION = (
    "This skill's entry menu is already queued. End the turn now without "
    "narrating, without calling ask_user_choice, and without repeating the "
    "options as text. The user's selection arrives as the next user message."
)

_MENU_SUPPRESSED_INSTRUCTION = (
    "No new menu was opened by this skill entry. Its entry menu was suppressed "
    "because the skill is already active or was already prompted. Continue "
    "the current request using existing answers when available. Follow the "
    "skill's recovery instructions if the user explicitly requests reopening. "
    "Do not claim a new menu is waiting."
)


def _skill_by_name(name: str) -> ActionSkill | None:
    slug = normalize_skill_name(name)
    return next((skill for skill in list_action_skills() if skill.name == slug), None)


def _open_entry_menu(menu: SkillEntryMenu, ctx: ActionToolScope) -> dict[str, Any]:
    """Queue ``menu`` through the public tool schema and executor, as a model call would."""
    args = menu.tool_args()
    validation_error = ask_user_choice_tool.validate_public_input(args)
    if validation_error is not None:
        return {"ok": False, "tool": _ENTRY_MENU_TOOL, "error": validation_error}
    outcome = execute_ask_user_choice_tool(args, ctx)
    payload: dict[str, Any] = dict(outcome) if isinstance(outcome, dict) else {"ok": bool(outcome)}
    payload.setdefault("ok", True)
    payload["tool"] = _ENTRY_MENU_TOOL
    return payload


def _hook_queued(hook: Mapping[str, Any] | None) -> bool:
    return hook is not None and bool(hook.get("ok")) and hook.get("menu") == "queued"


def entry_menu_queued(result: Mapping[str, Any]) -> bool:
    """True when the :func:`enter_skill` result queued the interactive selection menu."""
    hook = result.get("entry_menu")
    return isinstance(hook, Mapping) and _hook_queued(hook)


def _forget_entry_question(session: Any, menu: SkillEntryMenu) -> None:
    """Let a host-requested menu ask again what the session already answered.

    ``/demo`` and startup mean "ask me that question", so the session must drop
    its record of the answer; otherwise the menu tool refuses the hook and the
    shell shows nothing at all.
    """
    settled = getattr(session, "questions_already_answered", None)
    if not isinstance(settled, set):
        return
    title = menu.title.strip()
    if title:
        settled.discard(question_key(title))


def _may_open_menu(session: Any, skill: ActionSkill, *, from_model: bool) -> bool:
    """True when this entry may open the skill's entry menu.

    The host opens it on request. The model must not reopen one the session has
    already answered: a later message that routes back to the skill would ask
    the same question a second time.
    """
    if not from_model:
        return True
    return skill.name not in (getattr(session, "skills_already_prompted", None) or set())


def enter_skill(name: str, ctx: Any, *, from_model: bool = False) -> dict[str, Any]:
    """Activate ``name`` on the session, open its entry menu, and return the body for the model."""
    skill = _skill_by_name(name)
    body = load_skill_body(name) if skill is not None else ""
    if skill is None or not body:
        available = [item.name for item in list_action_skills()]
        return {
            "ok": False,
            "name": name,
            "error": f"unknown skill {name!r}",
            "available": available,
        }
    session = getattr(ctx, "session", None)
    already_active = (
        from_model and session is not None and getattr(session, "active_skill", None) == skill.name
    )
    # Re-entry retains the active skill and does not reopen an answered menu.
    if session is not None and not already_active:
        session.active_skill = skill.name
    menu = skill.entry_menu
    if menu is not None and not from_model:
        _forget_entry_question(session, menu)
    hook: dict[str, Any] | None = None
    if menu is not None and isinstance(ctx, ActionToolScope):
        if already_active or not _may_open_menu(session, skill, from_model=from_model):
            hook = {
                "ok": False,
                "tool": _ENTRY_MENU_TOOL,
                "menu": "suppressed",
                "reason": "already_active" if already_active else "already_prompted",
                "instruction": _MENU_SUPPRESSED_INSTRUCTION,
            }
        else:
            hook = _open_entry_menu(menu, ctx)
    queued = _hook_queued(hook)
    if queued and session is not None:
        already = getattr(session, "skills_already_prompted", None)
        if isinstance(already, set):
            already.add(skill.name)
    content = body
    if queued:
        content = "".join((body, "\n\n", MENU_QUEUED_INSTRUCTION))
    elif hook is not None and hook.get("menu") == "suppressed":
        content = "".join((body, "\n\n", _MENU_SUPPRESSED_INSTRUCTION))
    if not already_active:
        capture_skill_executed(
            skill_name=skill.name,
            entrypoint="model" if from_model else "host",
        )
    # ``summary`` is what the user sees; ``content`` is for the model only.
    # Without it the generic formatter prints the whole skill body on screen.
    result = {
        "ok": True,
        "name": skill.name,
        "summary": (
            f"the {skill.name} skill is already active"
            if already_active
            else f"loaded the {skill.name} skill"
        ),
        "content": content,
        "entry_menu": hook,
    }
    if already_active:
        result["already_active"] = True
    return result


__all__ = ["MENU_QUEUED_INSTRUCTION", "enter_skill", "entry_menu_queued"]
