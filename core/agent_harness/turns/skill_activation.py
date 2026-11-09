"""Retain skill context through menu answers and clear it for new requests."""

from __future__ import annotations

from typing import Any

from core.agent_harness.session.pending_choice import parse_ask_user_answers


def prepare_active_skill(session: Any, message: str) -> None:
    """Leave slash commands and menu answers in their current skill."""
    if not message.strip().startswith("/") and not parse_ask_user_answers(message):
        session.active_skill = None
