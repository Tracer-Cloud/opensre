"""Process-level snapshots for session trace spans (memory, threads, asyncio tasks).

``rss_mb`` is **current** resident set size (Linux ``/proc``), so turn-boundary
series can rise *and fall*; elsewhere it falls back to the peak watermark.
``rss_peak_mb`` is the POSIX ``ru_maxrss`` high-water mark (never decreases) —
useful context, not a leak signal by itself.
"""

from __future__ import annotations

import gc
import sys
import threading
from typing import Any

try:
    import resource as _resource
except ImportError:  # Windows / non-POSIX
    _resource = None  # type: ignore[assignment]

#: Cap thread rows embedded in a turn-boundary snapshot (ATM thread map).
_MAX_THREADS_IN_SNAPSHOT = 40

#: ``resource.getrusage`` units differ by platform; convert to mebibytes.
_BYTES_PER_KIBIBYTE = 1024
_KIB_PER_MIB = _BYTES_PER_KIBIBYTE
_BYTES_PER_MEBIBYTE = _BYTES_PER_KIBIBYTE * _KIB_PER_MIB
_RSS_MB_DECIMAL_PLACES = 2


def _normalize_ru_maxrss_mb(ru_maxrss: int) -> float:
    """Normalize ``resource.getrusage`` peak RSS to mebibytes (macOS vs Linux)."""
    if sys.platform == "darwin":
        # Darwin reports ``ru_maxrss`` in bytes.
        return round(ru_maxrss / _BYTES_PER_MEBIBYTE, _RSS_MB_DECIMAL_PLACES)
    # Linux reports ``ru_maxrss`` in kibibytes → divide by KiB/MiB.
    return round(ru_maxrss / _KIB_PER_MIB, _RSS_MB_DECIMAL_PLACES)


def _current_rss_mb() -> float | None:
    """Current process RSS in MiB from Linux ``/proc``, or ``None`` elsewhere."""
    if sys.platform.startswith("linux"):
        try:
            with open("/proc/self/status", encoding="utf-8") as fh:
                for line in fh:
                    if line.startswith("VmRSS:"):
                        # VmRSS is in kB
                        kib = int(line.split()[1])
                        return round(kib / _KIB_PER_MIB, _RSS_MB_DECIMAL_PLACES)
        except (OSError, ValueError, IndexError):
            # /proc unavailable or malformed: treat current RSS as unknown.
            return None
    return None


def sample_resource_snapshot() -> dict[str, Any]:
    """Current RSS (when available) + peak RSS + GC generation counts."""
    gen0, gen1, gen2 = gc.get_count()
    out: dict[str, Any] = {
        "gc_gen0": gen0,
        "gc_gen1": gen1,
        "gc_gen2": gen2,
    }
    current = _current_rss_mb()
    if current is not None:
        out["rss_mb"] = current

    if _resource is not None:
        ru_maxrss = _resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss
        peak = _normalize_ru_maxrss_mb(ru_maxrss)
        out["rss_peak_mb"] = peak
        # Last-resort: if current RSS unavailable, keep legacy field as peak
        # but mark it so consumers know it is a watermark.
        if "rss_mb" not in out:
            out["rss_mb"] = peak
            out["rss_is_peak"] = True
    return out


def sample_thread_snapshot(*, asyncio_tasks: int | None = None) -> dict[str, Any]:
    """Enumerate live threads for ATM thread-map spans.

    Includes per-thread ``ident``, ``name``, ``daemon``, and ``alive`` so the
    consumer can diff snapshots and show which workers appeared or vanished.
    """
    threads = list(threading.enumerate())
    main = threading.main_thread()
    rows: list[dict[str, Any]] = []
    for thread in threads[:_MAX_THREADS_IN_SNAPSHOT]:
        native_id = getattr(thread, "native_id", None)
        rows.append(
            {
                "ident": thread.ident,
                "name": thread.name,
                "daemon": thread.daemon,
                "alive": thread.is_alive(),
                **({"native_id": native_id} if native_id is not None else {}),
            }
        )
    if asyncio_tasks is None:
        asyncio_tasks = _running_asyncio_task_count()
    return {
        "thread_count": threading.active_count(),
        "daemon_count": sum(1 for t in threads if t.daemon),
        "main_thread_ident": main.ident,
        "asyncio_tasks": asyncio_tasks,
        "threads": rows,
        "threads_truncated": len(threads) > _MAX_THREADS_IN_SNAPSHOT,
    }


def sample_turn_boundary_stats(*, asyncio_tasks: int | None = None) -> dict[str, Any]:
    """Combined resource + thread snapshot for ``trace_span`` turn boundaries."""
    out = sample_resource_snapshot()
    out.update(sample_thread_snapshot(asyncio_tasks=asyncio_tasks))
    return out


def _running_asyncio_task_count() -> int:
    try:
        import asyncio

        loop = asyncio.get_running_loop()
    except RuntimeError:
        return 0
    return len(asyncio.all_tasks(loop))


__all__ = [
    "sample_resource_snapshot",
    "sample_thread_snapshot",
    "sample_turn_boundary_stats",
]
