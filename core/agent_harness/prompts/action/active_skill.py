"""Keep a skill's instructions available when its menu answer arrives."""

from __future__ import annotations

from core.agent_harness.prompts.skills import load_skill_body
from core.agent_harness.session.pending_choice import parse_ask_user_answers

_MAX_SKILL_CHARS = 24_000


def active_skill_block(name: str | None, message: str) -> str:
    """Return the answered skill's bounded instructions outside the cached prompt."""
    if not name or not parse_ask_user_answers(message):
        return ""
    body = load_skill_body(name)
    if not body:
        return ""
    return (
        f"ACTIVE SKILL: {name}\n"
        "The user is answering this skill's question. Continue from the answer "
        "with the skill's branch for it; do not reopen the question, restart "
        "steps completed in this run, or replay them because a plan still lists "
        "them. An explicit /demo starts a fresh run; results from earlier runs "
        "do not complete its steps. The skill decides the next tool call and "
        "whether it owns the live plan.\n\n"
        f"{body[:_MAX_SKILL_CHARS]}"
    )
