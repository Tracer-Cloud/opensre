from __future__ import annotations

import os


def infrastructure_available() -> bool:
    """Return True when AWS infrastructure is available (not CI and not explicitly skipped)."""
    return not (os.getenv("CI") or os.getenv("SKIP_INFRA_TESTS"))
