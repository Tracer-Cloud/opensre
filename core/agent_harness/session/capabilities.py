"""Which capabilities a host offers on a session.

``SessionCore.available_capabilities`` maps a capability name to the tools the
host offers for it; an empty tuple means "this host has none". The harness
reads that map when it plans a turn, so the shape belongs here — a host states
what it withholds and never pokes the mapping itself.
"""

from __future__ import annotations

from typing import Any


def withhold_capabilities(session: Any, *names: str) -> None:
    """Record that this host offers no tools for ``names``.

    Idempotent, so a host may state its policy wherever a session is prepared
    without tracking whether an earlier call already did. A session without the
    mapping (a stub in a test) is left alone rather than grown one.
    """
    capabilities = getattr(session, "available_capabilities", None)
    if not isinstance(capabilities, dict):
        return
    for name in names:
        capabilities[name] = ()


__all__ = ["withhold_capabilities"]
