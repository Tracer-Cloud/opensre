"""Keep a skill's instructions available when its menu answer arrives."""

from __future__ import annotations

from core.agent_harness.prompts.skills import load_skill_body
from core.agent_harness.session.pending_choice import parse_ask_user_answers

_MAX_SKILL_CHARS = 24_000


#: The answer continues the plan this skill wrote: the host advances it.
_HOST_ADVANCES_PLAN = (
    "The host moves the plan to the next step when you call that step's tool; "
    "send update_plan only to create or revise the plan, mark a step blocked, "
    "or settle it, and in the same response as a tool call."
)
#: Anything else: the model writes its own status transitions.
_PAIR_PLAN_WRITES = (
    "Send each update_plan in the same response as the tool call of the step it starts."
)


def active_skill_block(name: str | None, message: str, *, host_advances: bool = False) -> str:
    """Return the answered skill's bounded instructions outside the cached prompt.

    ``host_advances`` says the answer continues the plan this skill owns, so
    the host moves that plan forward and no status write is asked for.
    """
    if not name or not parse_ask_user_answers(message):
        return ""
    body = load_skill_body(name)
    if not body:
        return ""
    plan_rule = _HOST_ADVANCES_PLAN if host_advances else _PAIR_PLAN_WRITES
    return (
        f"ACTIVE SKILL: {name}\n"
        "The user is answering this skill's question. Continue from the answer "
        "with the skill's branch for it; do not reopen the question, restart "
        "steps completed in this run, or replay them because a plan still lists "
        "them. An explicit /demo starts a fresh run; results from earlier runs "
        "do not complete its steps. The skill decides the next tool call and "
        "whether it owns the live plan. Its instructions are below, so do not "
        "load it again with skill_view; when this answer chose it from the "
        f"onboarding menu, start its first step now. {plan_rule}\n\n"
        f"{body[:_MAX_SKILL_CHARS]}"
    )
