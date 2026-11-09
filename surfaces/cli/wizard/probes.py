"""Reachability probes for the quickstart wizard."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path

from surfaces.shared.llm_setup.catalog import PROJECT_ENV_PATH


@dataclass(frozen=True)
class ProbeResult:
    """A lightweight reachability result."""

    target: str
    reachable: bool
    detail: str

    def as_dict(self) -> dict[str, object]:
        """Convert the result to a JSON-friendly dict."""
        return asdict(self)


def _is_writable(path: Path) -> bool:
    if path.exists():
        return os.access(path, os.W_OK)
    parent = path.parent
    while not parent.exists() and parent != parent.parent:
        parent = parent.parent
    return os.access(parent, os.W_OK)


def probe_local_target(store_path: Path) -> ProbeResult:
    """Check whether the local wizard targets are writable."""
    writable = _is_writable(store_path) and _is_writable(PROJECT_ENV_PATH)
    detail = f"Local config targets: {store_path} and {PROJECT_ENV_PATH}"
    if not writable:
        detail = f"Local config is not writable: {store_path} or {PROJECT_ENV_PATH}"
    return ProbeResult(target="local", reachable=writable, detail=detail)
