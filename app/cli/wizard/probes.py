"""Reachability probes for the quickstart wizard."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

from app.cli.wizard.config import PROJECT_ENV_PATH
from app.config import get_tracer_base_url


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


def probe_remote_target(timeout_seconds: float = 3.0) -> ProbeResult:
    """Probe the hosted Tracer base URL used for future remote setup."""
    url = get_tracer_base_url()
    request = Request(url, method="GET")
    try:
        with urlopen(request, timeout=timeout_seconds) as response:
            status = getattr(response, "status", 200)
            return ProbeResult(
                target="remote",
                reachable=200 <= status < 500,
                detail=f"Tracer remote target reachable at {url} (HTTP {status})",
            )
    except URLError as err:
        reason = getattr(err, "reason", err)
        return ProbeResult(
            target="remote",
            reachable=False,
            detail=f"Tracer remote target unreachable at {url}: {reason}",
        )
