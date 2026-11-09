"""Message context prefix strippers.

Gateway surfaces (Slack, etc.) may prepend a channel/context marker to the raw
message text (e.g. ``[Slack channel_id=…]``) before it reaches core prompt/turn
helpers. Core needs to strip that marker to evaluate the underlying text (for
example, matching a bare affirmative like "yes") without hardcoding any vendor's
marker format.
"""

from __future__ import annotations

from collections.abc import Callable

MessageContextPrefixStripper = Callable[[str], "tuple[str, str] | None"]

_message_context_prefix_strippers: list[MessageContextPrefixStripper] = []


def register_message_context_prefix_stripper(fn: MessageContextPrefixStripper) -> None:
    _message_context_prefix_strippers.append(fn)


def clear_message_context_prefix_strippers() -> None:
    _message_context_prefix_strippers.clear()


def strip_message_context_prefix(text: str) -> tuple[str, str]:
    """Return ``(prefix, remainder)``, trying registered strippers in order.

    The first stripper to return a non-``None`` result wins. Falls back to
    ``("", text)`` when no registered stripper matches.
    """
    for stripper in _message_context_prefix_strippers:
        result = stripper(text)
        if result is not None:
            return result
    return "", text


def reset() -> None:
    """Clear registered strippers (tests)."""
    clear_message_context_prefix_strippers()


__all__ = [
    "MessageContextPrefixStripper",
    "clear_message_context_prefix_strippers",
    "register_message_context_prefix_stripper",
    "strip_message_context_prefix",
]
