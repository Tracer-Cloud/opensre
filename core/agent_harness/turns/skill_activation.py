"""Retain skill context through menu answers and clear it for new requests."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers


def skill_continues(active_skill: str | None, message: str) -> bool:
    """Whether ``message`` keeps the turn inside ``active_skill``.

    A slash command or a menu answer stays in the skill; any other message is
    a new request.
    """
    if not active_skill:
        return False
    return message.strip().startswith("/") or bool(parse_ask_user_answers(message))


def prepare_active_skill(session: Any, message: str) -> None:
    """Leave slash commands and menu answers in their current skill."""
    if not skill_continues(getattr(session, "active_skill", None), message):
        session.active_skill = None
