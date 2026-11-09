"""Shared per-turn steps every chat transport runs.

Sits between inbound platform events and turn execution: identity policy,
approvals, attention, conversation locks, stop/cancel. Transports call these
steps and add only vendor-specific parsing and rendering.
"""

from __future__ import annotations

__all__: list[str] = []
