"""Token-count constants and short-form formatter.

Shared between the streaming renderer (ui/streaming.py) and the spinner state
(runtime/state.py) so both display the same ``1.2k`` format and the same
chars-per-token heuristic.
"""

from __future__ import annotations

# Approximate characters per token used to estimate token counts from byte
# lengths without waiting for the API to return exact usage.
_CHARS_PER_TOKEN = 4


def format_token_count_short(token_count: int) -> str:
    """Format a token count as a short string — ``42`` / ``1.2k`` / ``5.2k``."""
    if token_count >= 1000:
        return f"{token_count / 1000:.1f}k"
    return str(token_count)


__all__ = ["_CHARS_PER_TOKEN", "format_token_count_short"]
