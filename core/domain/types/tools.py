"""Tool-related type aliases."""

from __future__ import annotations

from enum import StrEnum


class ToolSurface(StrEnum):
    """Closed set of surfaces a registered tool can be exposed on."""

    CHAT = "chat"
    ACTION = "action"


class ToolRole(StrEnum):
    """What one tool call is to the model response that requested it.

    ``ACTION`` and ``BOOKKEEPING`` calls (plan, memory, goal ticks) may be
    batched in one response and run sequentially in provider order. A
    ``TURN_ENDING`` call hands control to the user and must be the only call
    in its response.
    """

    ACTION = "action"
    BOOKKEEPING = "bookkeeping"
    TURN_ENDING = "turn_ending"
