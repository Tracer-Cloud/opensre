"""Turn-end memory readings, to size how many turns a task can run at once.

A Fargate task's memory limit is enforced against its container cgroup, which
also counts child processes a turn starts (Codex CLI, git clones, sandboxes) —
this process's own resident memory (RSS) misses them. So the container cgroup's
current and peak usage are the sizing numbers, and the process RSS delta is a
secondary signal for the Python side alone.

The cgroup peak is the container's lifetime high-water mark, and concurrent
turns share one process, so no reading here is attributable to a single turn.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass
from pathlib import Path

try:
    import resource as _resource
except ImportError:  # Windows / non-POSIX
    _resource = None  # type: ignore[assignment]

_BYTES_PER_MB = 1_048_576

# Mount point of the container's own cgroup hierarchy (Linux only).
_CGROUP_ROOT = Path("/sys/fs/cgroup")
# Each tuple names the cgroup v2 file first, then the v1 memory-controller file.
_CGROUP_CURRENT_FILES = ("memory.current", "memory/memory.usage_in_bytes")
_CGROUP_PEAK_FILES = ("memory.peak", "memory/memory.max_usage_in_bytes")


@dataclass(frozen=True, slots=True)
class TurnMemory:
    """Memory readings taken as a turn ends; each is ``None`` where unreadable."""

    container_bytes: int | None
    container_peak_bytes: int | None
    process_rss_delta_bytes: int | None


def _read_cgroup_bytes(relative_paths: tuple[str, ...]) -> int | None:
    """The first readable byte count among ``relative_paths`` under the cgroup root."""
    for relative_path in relative_paths:
        try:
            return int((_CGROUP_ROOT / relative_path).read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            continue
    return None


def resident_memory_bytes() -> int | None:
    """This process's current resident memory in bytes, or ``None`` if unknown.

    Reads ``/proc/self/statm`` for an accurate live reading on Linux (the Fargate
    runtime); returns ``None`` where the required platform APIs are absent so
    callers skip the measurement rather than report a wrong number.
    """
    if _resource is None:
        return None
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            resident_pages = int(statm.read().split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return resident_pages * _resource.getpagesize()


def peak_resident_memory_bytes() -> int | None:
    """This process's peak resident memory in bytes, or ``None`` if unknown."""
    if _resource is None:
        return None
    try:
        peak = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
    except (OSError, ValueError):
        return None
    # Linux reports kibibytes; macOS and the BSDs report bytes.
    return peak if sys.platform == "darwin" else peak * 1024


def _mb(value: int | None) -> str:
    return "unknown" if value is None else f"{value / _BYTES_PER_MB:.1f}"


def record_turn_memory(logger: logging.Logger, rss_before: int | None) -> TurnMemory:
    """Read and DEBUG-log turn-end memory, given the process RSS taken at turn start.

    Never raises: a reading the platform cannot provide (e.g. macOS dev has no
    cgroup or ``/proc``) is ``None``, so the caller can record the result on
    both the success and the failure path without guarding.
    """
    rss_after = resident_memory_bytes()
    memory = TurnMemory(
        container_bytes=_read_cgroup_bytes(_CGROUP_CURRENT_FILES),
        container_peak_bytes=_read_cgroup_bytes(_CGROUP_PEAK_FILES),
        process_rss_delta_bytes=(
            rss_after - rss_before if rss_before is not None and rss_after is not None else None
        ),
    )
    logger.debug(
        "gateway_turn_memory container_mb=%s container_peak_mb=%s "
        "rss_start_mb=%s rss_end_mb=%s rss_delta_mb=%s rss_peak_mb=%s",
        _mb(memory.container_bytes),
        _mb(memory.container_peak_bytes),
        _mb(rss_before),
        _mb(rss_after),
        _mb(memory.process_rss_delta_bytes),
        _mb(peak_resident_memory_bytes()),
    )
    return memory


__all__ = [
    "TurnMemory",
    "peak_resident_memory_bytes",
    "record_turn_memory",
    "resident_memory_bytes",
]
