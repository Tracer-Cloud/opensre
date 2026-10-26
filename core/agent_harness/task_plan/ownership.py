"""Whether an Ask User answer continues the stored plan's own workflow.

The host advances a plan only for the workflow working it. A plan written
this turn is that workflow's by construction; otherwise the turn must be an
answer to a question the plan's owner skill asked, with that skill still
active. An answer to another workflow's menu, a plain new message, or a
skill-less plan leaves the model writing its own status transitions.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers, question_key
from core.agent_harness.task_plan.plan import TaskPlan


def answer_continues_plan(
    plan: TaskPlan | None,
    *,
    active_skill: str | None,
    skill_question_keys: Mapping[str, Any] | None,
    turn_user_message: str,
    plan_only: bool,
) -> bool:
    """True when this turn answers a question the open plan's owner skill asked."""
    if not isinstance(plan, TaskPlan) or plan.is_settled or plan_only:
        return False
    owner = plan.owner
    if not owner or owner != active_skill:
        return False
    answers = parse_ask_user_answers(turn_user_message)
    if not answers:
        return False
    asked = (skill_question_keys or {}).get(owner)
    if not isinstance(asked, (set, frozenset)):
        return False
    return all(question_key(title) in asked for title, _answer in answers)


def session_answer_continues_plan(session: Any, turn_user_message: str) -> bool:
    """:func:`answer_continues_plan` over the session's live fields."""
    keys = getattr(session, "skill_question_keys", None)
    return answer_continues_plan(
        getattr(session, "task_plan", None),
        active_skill=getattr(session, "active_skill", None),
        skill_question_keys=keys if isinstance(keys, Mapping) else None,
        turn_user_message=turn_user_message,
        plan_only=bool(getattr(session, "plan_only_until_authorized", False)),
    )


__all__ = ["answer_continues_plan", "session_answer_continues_plan"]
