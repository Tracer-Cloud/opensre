"""Seed gateway sessions from Discord thread history when local state is empty."""

from __future__ import annotations

import logging

from core.agent_harness import SessionCore
from gateway.core.session import seed_session_history

logger = logging.getLogger(__name__)


def session_needs_thread_seed(text: str, *, is_reply: bool) -> bool:
    """Whether to pull Discord thread history into the session before the turn."""
    _ = text
    return is_reply


def seed_session_from_discord_thread(
    session: SessionCore,
    *,
    history: list[tuple[str, str]],
) -> int:
    """Append prior thread messages as user/assistant pairs. Returns count added."""
    return seed_session_history(session, history)
