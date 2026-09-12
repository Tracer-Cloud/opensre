"""Tool-related type aliases."""

from __future__ import annotations

from enum import StrEnum


class ToolSurface(StrEnum):
    """Closed set of surfaces a registered tool can be exposed on."""

    CHAT = "chat"
    ACTION = "action"


class ToolRole(StrEnum):
    """What one tool call is to the model response that requested it.

    The runtime executes at most one ``ACTION`` per response. ``BOOKKEEPING``
    calls (plan, memory, goal ticks) may accompany that action; a
    ``TURN_ENDING`` call hands control to the user and must be the only call.
    """

    ACTION = "action"
    BOOKKEEPING = "bookkeeping"
    TURN_ENDING = "turn_ending"
