"""Non-secret setup outcomes passed from a terminal child to its parent REPL."""

from __future__ import annotations

import json
import os
from pathlib import Path

from config.constants.llm import OPENSRE_MODEL_SETUP_RESULT_ENV


def notify_setup(*, connected: str | None = None, selected: bool = False) -> None:
    """Write only to the result path created for this invocation by its parent."""
    name = os.getenv(OPENSRE_MODEL_SETUP_RESULT_ENV)
    if not name:
        return
    path = Path(name)
    try:
        previous = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        previous = {}
    result = previous if isinstance(previous, dict) else {}
    if connected is not None:
        result["connected"] = connected
    if selected:
        result["selected"] = True
    path.write_text(json.dumps(result), encoding="utf-8")


def read_setup(path: Path) -> tuple[str | None, bool]:
    """Ignore malformed/absent outcomes; never infer success from terminal prose."""
    try:
        result = json.loads(path.read_text())
    except (ValueError, OSError):
        return None, False
    if not isinstance(result, dict):
        return None, False
    connected = result.get("connected")
    return connected if isinstance(connected, str) else None, result.get("selected") is True
