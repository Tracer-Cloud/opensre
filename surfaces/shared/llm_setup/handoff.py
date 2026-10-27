"""Non-secret setup outcomes passed from a terminal child to its parent REPL."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from config.constants.llm import OPENSRE_MODEL_SETUP_RESULT_ENV


@dataclass(frozen=True)
class SetupOutcome:
    """Provider identity and changed setting names, never credential values."""

    connected: str | None = None
    selected: bool = False
    written_keys: tuple[str, ...] = ()


def notify_setup(
    *,
    connected: str | None = None,
    selected: bool = False,
    written_keys: tuple[str, ...] = (),
) -> None:
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
    previous_keys = result.get("written_keys", [])
    known = (
        {key for key in previous_keys if isinstance(key, str)}
        if isinstance(previous_keys, list)
        else set()
    )
    result["written_keys"] = sorted(known | set(written_keys))
    path.write_text(json.dumps(result), encoding="utf-8")


def read_setup(path: Path) -> SetupOutcome:
    """Ignore malformed/absent outcomes; never infer success from terminal prose."""
    try:
        result = json.loads(path.read_text())
    except (ValueError, OSError):
        return SetupOutcome()
    if not isinstance(result, dict):
        return SetupOutcome()
    connected = result.get("connected")
    raw_keys = result.get("written_keys", [])
    keys = (
        tuple(key for key in raw_keys if isinstance(key, str)) if isinstance(raw_keys, list) else ()
    )
    return SetupOutcome(
        connected if isinstance(connected, str) else None,
        result.get("selected") is True,
        keys,
    )
