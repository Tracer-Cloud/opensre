"""Transport-neutral session history seeding."""

from __future__ import annotations

from collections.abc import Iterable

from core.agent_harness import SessionCore

_MESSAGE_ROLES = frozenset({"user", "assistant"})


def seed_session_history(
    session: SessionCore,
    history: Iterable[tuple[str, str]],
    *,
    replace_existing: bool = False,
) -> int:
    """Seed canonical conversation tuples and return the number stored."""
    messages = [
        (role, content) for role, content in history if role in _MESSAGE_ROLES and content.strip()
    ]
    if not messages:
        return 0
    if session.cli_agent_messages and not replace_existing:
        return 0
    session.cli_agent_messages = messages
    return len(messages)


__all__ = ["seed_session_history"]
