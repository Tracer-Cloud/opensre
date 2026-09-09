"""Keep an answer turn inside the skill that asked the question.

When a skill loaded by ``skill_view`` declares its tools, the turn that
carries the user's menu answer offers only those tools plus the harness's
own. Without this the planner sees every tool again and can wander into an
unrelated skill on the strength of one word in the answer. A genuine new user
turn clears the scope.
"""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers

_ALWAYS_OFFERED = frozenset(
    {
        "ask_user_choice",
        "update_plan",
        "skill_view",
        "memory_remember",
        "memory_recall",
        "session_goal_complete",
        "task_cancel",
    }
)


def scope_tools_to_active_skill(tools: list[Any], session: Any, message: str) -> list[Any]:
    """Filter ``tools`` for an answer turn inside a skill; reset the scope otherwise."""
    if message.strip() == "/choose" and getattr(session, "pending_user_choice", None) is not None:
        # The literal transport command needs slash_invoke, but is not a new request.
        return tools
    declared = tuple(getattr(session, "active_skill_tools", ()) or ())
    if not parse_ask_user_answers(message):
        session.active_skill = None
        session.active_skill_tools = ()
        return tools
    if not declared:
        return tools
    allowed = set(declared) | _ALWAYS_OFFERED
    return [tool for tool in tools if getattr(tool, "name", None) in allowed]


__all__ = ["scope_tools_to_active_skill"]
