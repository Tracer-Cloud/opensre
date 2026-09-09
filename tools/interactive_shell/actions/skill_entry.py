"""Enter an action-agent skill: activate it on the session and run its ``pre_execute`` hooks.

One entry point serves every way into a skill. The model enters through the
``skill_view`` tool; the host enters directly (interactive startup, ``/demo``)
with no model step and no tool-event render. A skill's ``pre_execute`` calls run
here through the real tool executors, so a hook-queued menu behaves exactly as
if the model had called the tool. Only allowlisted tools may run from a hook.
"""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Any

from core.agent_harness.spi.grounding import ActionSkill, list_action_skills, load_skill_body
from core.agent_harness.tools import ActionToolScope, ToolExecutor
from core.tool import RegisteredTool
from tools.interactive_shell.actions.ask_choice import (
    ask_user_choice_tool,
    execute_ask_user_choice_tool,
)

# Allowlisted hook tools: the registered tool (its public schema gates the
# frontmatter args exactly as it gates a model call) and the executor to run.
_PRE_EXECUTE_TOOLS: Mapping[str, tuple[RegisteredTool, ToolExecutor]] = MappingProxyType(
    {"ask_user_choice": (ask_user_choice_tool, execute_ask_user_choice_tool)}
)

MENU_QUEUED_INSTRUCTION = (
    "A menu declared by this skill's pre_execute is already queued. End the turn "
    "now without narrating, without calling ask_user_choice, and without "
    "repeating the options as text. The user's selection arrives as the next "
    "user message."
)


def _skill_by_name(name: str) -> ActionSkill | None:
    slug = name.strip().lower().replace("_", "-")
    return next((skill for skill in list_action_skills() if skill.name == slug), None)


def _run_pre_execute(skill: ActionSkill, ctx: ActionToolScope) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for call in skill.pre_execute:
        allowed = _PRE_EXECUTE_TOOLS.get(call.tool)
        if allowed is None:
            results.append(
                {"ok": False, "tool": call.tool, "error": "pre_execute tool not allowed"}
            )
            continue
        tool, executor = allowed
        args = dict(call.args)
        validation_error = tool.validate_public_input(args)
        if validation_error is not None:
            results.append({"ok": False, "tool": call.tool, "error": validation_error})
            continue
        outcome = executor(args, ctx)
        payload: dict[str, Any] = (
            dict(outcome) if isinstance(outcome, dict) else {"ok": bool(outcome)}
        )
        payload.setdefault("ok", True)
        payload["tool"] = call.tool
        results.append(payload)
    return results


def pre_execute_queued_menu(results: list[dict[str, Any]]) -> bool:
    """True when a ``pre_execute`` hook queued the interactive selection menu."""
    return any(item.get("ok") and item.get("menu") == "queued" for item in results)


def enter_skill(name: str, ctx: Any) -> dict[str, Any]:
    """Activate ``name`` on the session, run its hooks, and return the body for the model."""
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
    # The flow is now inside this skill: the next answer turn offers only its tools.
    session = getattr(ctx, "session", None)
    if session is not None:
        session.active_skill = skill.name
        session.active_skill_tools = tuple(skill.tools)
    hooks = (
        _run_pre_execute(skill, ctx)
        if skill.pre_execute and isinstance(ctx, ActionToolScope)
        else []
    )
    content = body
    if pre_execute_queued_menu(hooks):
        content = "".join((body, "\n\n", MENU_QUEUED_INSTRUCTION))
    # ``summary`` is what the user sees; ``content`` is for the model only.
    # Without it the generic formatter prints the whole skill body on screen.
    return {
        "ok": True,
        "name": skill.name,
        "summary": f"loaded the {skill.name} skill",
        "content": content,
        "pre_execute": hooks,
    }


__all__ = ["MENU_QUEUED_INSTRUCTION", "enter_skill", "pre_execute_queued_menu"]
