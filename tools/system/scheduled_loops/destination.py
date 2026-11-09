"""Where a schedule made from a chat turn delivers: the conversation it was asked in."""

from __future__ import annotations

from typing import Any


def conversation_destination(session: Any) -> tuple[str, str]:
    """``(provider, chat_id)`` of the chat this turn came from; empty strings off chat.

    The gateway stamps both on the session's integration cache each turn.
    """
    resolved = getattr(session, "resolved_integrations_cache", None) or {}
    provider = str(resolved.get("_gateway_platform") or "").strip().lower()
    chat_id = str(resolved.get("_gateway_chat_id") or "").strip()
    return provider, chat_id


__all__ = ["conversation_destination"]
