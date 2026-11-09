"""Per-component state the running gateway publishes for other processes to read.

Written from inside the daemon, read from outside it — the CLI's
``gateway status`` and the shell's ``/gateway``. A status file left behind by a
process that has since died reads as no status rather than stale truth.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from config.constants import OPENSRE_HOME_DIR
from gateway.core.process.liveness import process_is_alive

GATEWAY_COMPONENTS_FILE: Path = OPENSRE_HOME_DIR / "gateway" / "components.json"


def write_component_status(components: dict[str, str]) -> None:
    """Record the running process's per-component state (called by the controller)."""
    GATEWAY_COMPONENTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {"pid": os.getpid(), "started_at": time.time(), "components": components}
    GATEWAY_COMPONENTS_FILE.write_text(json.dumps(payload, indent=2))


def read_component_status() -> dict[str, str]:
    """Return the live process's component states ({} when it is not running)."""
    try:
        payload = json.loads(GATEWAY_COMPONENTS_FILE.read_text())
        pid = int(payload["pid"])
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    if pid <= 0 or not process_is_alive(pid):
        return {}
    return {str(name): str(detail) for name, detail in payload.get("components", {}).items()}


def clear_component_status() -> None:
    GATEWAY_COMPONENTS_FILE.unlink(missing_ok=True)


__all__ = [
    "GATEWAY_COMPONENTS_FILE",
    "clear_component_status",
    "read_component_status",
    "write_component_status",
]
