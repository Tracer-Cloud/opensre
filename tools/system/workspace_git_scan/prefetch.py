"""Workspace scans started before the model calls ``scan_local_git_workspace``."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from config.constants.tooling import (
    TOOL_PREFETCH_MAX_ENTRIES,
    WORKSPACE_SCAN_PREFETCH_MAX_AGE_SECONDS,
)
from core.tool_framework.utils import PrefetchRegistry
from tools.system.workspace_git_scan.scan import WorkspaceSnapshot


@dataclass(frozen=True)
class ScanRequest:
    """The arguments one scan runs with; a prefetch answers only an identical call."""

    root: Path
    days: int
    skip_paths: frozenset[Path]


_SCANS: PrefetchRegistry[ScanRequest, WorkspaceSnapshot] = PrefetchRegistry(
    name="workspace-scan",
    max_age_seconds=WORKSPACE_SCAN_PREFETCH_MAX_AGE_SECONDS,
    max_entries=TOOL_PREFETCH_MAX_ENTRIES,
)


def start_scan_prefetch(
    request: ScanRequest, work: Callable[[Callable[[], bool]], WorkspaceSnapshot]
) -> bool:
    """Run ``work`` in the background for ``request``; False when one is already kept."""
    return _SCANS.start(request, work)


def claim_scan_prefetch(
    request: ScanRequest, *, should_stop: Callable[[], bool] | None
) -> WorkspaceSnapshot | None:
    """The prefetched snapshot for ``request``, or None when the tool must scan itself."""
    return _SCANS.claim(request, should_stop=should_stop)


def reset_scan_prefetch() -> None:
    """Forget every prefetched scan (test isolation)."""
    _SCANS.reset()


__all__ = ["ScanRequest", "claim_scan_prefetch", "reset_scan_prefetch", "start_scan_prefetch"]
