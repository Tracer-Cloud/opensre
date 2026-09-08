"""Attach a structured conversational SessionGoal and tick its checklist."""

from __future__ import annotations

from typing import Any

from core.agent_harness.spi.session_goal import (
    SessionGoal,
    attach_session_goal,
    build_session_goal,
    session_goal_is_attached,
)
from core.agent_harness.tools import ActionToolScope, execute_with_action_context
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel
from core.tool_framework.utils import object_schema, string_array_property, string_property


def execute_session_goal_tool(args: dict[str, Any], ctx: ActionToolScope) -> dict[str, Any]:
    existing = getattr(ctx.session, "session_goal", None)
    if session_goal_is_attached(ctx.session):
        return {
            "ok": True,
            "attached": False,
            "reason": "an active or paused session goal is already attached",
            "condition": getattr(existing, "condition", ""),
        }

    condition = str(args.get("condition", "")).strip()
    if not condition:
        return {"ok": False, "error": "condition is required"}
    raw_items = args.get("items")
    items = tuple(str(item).strip() for item in raw_items) if isinstance(raw_items, list) else ()
    raw_max_turns = args.get("max_turns")
    max_turns = (
        raw_max_turns
        if isinstance(raw_max_turns, int) and not isinstance(raw_max_turns, bool)
        else None
    )
    goal = attach_session_goal(
        ctx.session,
        build_session_goal(
            condition, checklist=items, max_outer_turns=max_turns
        ).with_bookkeeping_call(),
    )
    return {
        "ok": True,
        "attached": True,
        "condition": goal.condition,
        "items": list(goal.checklist),
        "max_turns": goal.max_outer_turns,
    }


def run_session_goal(
    *,
    condition: str,
    context: Any,
    items: list[str] | None = None,
    max_turns: int | None = None,
) -> dict[str, Any]:
    return execute_with_action_context(
        {
            "condition": condition,
            "items": items or [],
            "max_turns": max_turns,
        },
        context,
        execute_session_goal_tool,
    )


def execute_session_goal_complete_tool(
    args: dict[str, Any], ctx: ActionToolScope
) -> dict[str, Any]:
    goal = getattr(ctx.session, "session_goal", None)
    if not isinstance(goal, SessionGoal) or not session_goal_is_attached(ctx.session):
        return {"ok": False, "error": "no active goal"}
    raw_items = args.get("items")
    if not isinstance(raw_items, list) or not raw_items:
        return {"ok": False, "error": "items must be a non-empty list of checklist indices"}
    indices: set[int] = set()
    for value in raw_items:
        try:
            index = int(value)
        except (TypeError, ValueError):
            return {"ok": False, "error": "items must be checklist indices"}
        if goal.checklist and (index < 0 or index >= len(goal.checklist)):
            return {"ok": False, "error": f"index {index} is not on the checklist"}
        indices.add(index)
    updated = attach_session_goal(
        ctx.session, goal.with_completed(goal.completed | indices).with_bookkeeping_call()
    )
    return {
        "ok": True,
        "completed": sorted(updated.completed),
        "checklist_complete": updated.checklist_complete,
        "unfinished": [item for _index, item in updated.unfinished_items],
    }


def run_session_goal_complete(
    *,
    items: list[int],
    context: Any,
) -> dict[str, Any]:
    return execute_with_action_context(
        {"items": items},
        context,
        execute_session_goal_complete_tool,
    )


session_goal_complete_tool = RegisteredTool(
    name="session_goal_complete",
    description=(
        "Mark one or more /goal checklist items done by 0-based index. "
        "Use this instead of writing session_goal:done= tags in the reply."
    ),
    use_cases=[
        "A checklist step finished after a successful tool call",
        "Several items completed in the same turn",
    ],
    anti_examples=[
        "Claiming the whole goal is met in prose",
        "Ticking an item that did not happen",
    ],
    input_schema=object_schema(
        properties={
            "items": {
                "type": "array",
                "items": {"type": "integer"},
                "minItems": 1,
                "description": "0-based checklist indices that this turn completed.",
            },
        },
        required=("items",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_session_goal_complete,
)


session_goal_tool = RegisteredTool(
    name="session_goal_set",
    description=(
        "Attach a cross-turn conversational goal for a checklist or walkthrough "
        "the user asked to continue without pausing. Do not use for local shell "
        "work or a single-turn answer."
    ),
    use_cases=[
        (
            "User asks to walk a multi-step checklist or keep going across turns "
            "until a finish condition is met (e.g. a 5-step sequential process)"
        ),
        (
            "Action handoff needs a durable SessionGoal so the host continues "
            "outer turns until the checklist is done"
        ),
    ],
    anti_examples=[
        "One-shot Q&A or a single lookup that finishes this turn",
        "Local shell / code-edit work that should use shell_run or update_plan",
        "User only wants a written plan with no execution (use update_plan)",
    ],
    input_schema=object_schema(
        properties={
            "condition": string_property(
                description="The user's requested completion condition.",
                min_length=1,
            ),
            "items": string_array_property(
                description="Optional checklist items in completion order.",
            ),
            "max_turns": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional hard cap on outer chat turns.",
            },
        },
        required=("condition",),
    ),
    source="interactive_shell",
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    parallel_safe=False,
    accepts_runtime_context=True,
    run=run_session_goal,
)


__all__ = [
    "execute_session_goal_complete_tool",
    "execute_session_goal_tool",
    "session_goal_complete_tool",
    "session_goal_tool",
]
