"""Contracts shared by the integration scans that write workspace knowledge."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

#: Turns a prompt into a model reply. Scans take one optionally and still
#: write their deterministic facts without it.
Summarize = Callable[[str], str]


@dataclass(frozen=True)
class ScanOutcome:
    """What one integration scan did. ``error`` is short and safe to show a member."""

    service: str
    ok: bool
    written: tuple[str, ...] = ()
    error: str = ""
    details: dict[str, int] = field(default_factory=dict)


__all__ = ["ScanOutcome", "Summarize"]
