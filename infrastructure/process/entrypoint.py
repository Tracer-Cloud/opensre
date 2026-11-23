"""Re-enter the current OpenSRE installation from Python or a frozen executable."""

from __future__ import annotations

import sys


def opensre_command(*args: str) -> list[str]:
    """Build a child command without sending Python module flags to a frozen binary."""
    prefix = [sys.executable]
    if not getattr(sys, "frozen", False):
        # ``-P`` keeps the working directory off the module path: it is often a
        # repository someone else wrote, and a ``surfaces/`` package there would
        # otherwise run in place of this installation.
        prefix.extend(["-P", "-m", "surfaces.entrypoint"])
    return [*prefix, *args]
