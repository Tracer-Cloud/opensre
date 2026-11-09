"""Input normalization and validation for Rocket.Chat message actions."""

from __future__ import annotations

from infrastructure.delivery.notifications.limits import MAX_MESSAGE_SIZE
from infrastructure.text.truncation import truncate


def normalize_optional_text(value: str) -> str:
    return str(value or "").strip()


def validate_message(message: str) -> tuple[bool, str, str]:
    """Return (valid, normalized-and-truncated message, error).

    Truncation happens here — before length reporting — so the
    ``message_length`` in tool results always reflects the text actually
    submitted for delivery, not the pre-truncation input.
    """
    normalized = str(message or "").strip()
    if not normalized:
        return False, "", "Message cannot be empty."
    return True, truncate(normalized, MAX_MESSAGE_SIZE, suffix="…"), ""
