"""System metrics collection for the remote health endpoint.

All metrics are gathered using Python stdlib only — no external
dependencies.  Each collector returns ``None`` on failure so the
endpoint degrades gracefully on any platform.
"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

_resource: Any | None

try:
    import resource as _resource
except ModuleNotFoundError:
    _resource = None


def collect_system_metrics() -> dict[str, Any]:
    """Return a snapshot of host and process metrics.

    Safe to call on Linux (EC2) and macOS (dev).  Individual sections
    are omitted when the underlying OS primitive is unavailable.
    """
    metrics: dict[str, Any] = {}

    cpu = _collect_cpu()
    if cpu is not None:
        metrics["cpu"] = cpu

    memory = _collect_memory()
    if memory is not None:
        metrics["memory"] = memory

    disk = _collect_disk()
    if disk is not None:
        metrics["disk"] = disk

    uptime = _collect_uptime()
    if uptime is not None:
        metrics["uptime"] = uptime

    metrics["platform"] = _collect_platform()

    process = _collect_process()
    if process is not None:
        metrics["process"] = process

    return metrics


# ---------------------------------------------------------------------------
# CPU
# ---------------------------------------------------------------------------


def _collect_cpu() -> dict[str, Any] | None:
    getloadavg = getattr(os, "getloadavg", None)
    if getloadavg is None:
        return None

    try:
        load1, load5, load15 = getloadavg()
        return {
            "load_avg_1m": round(load1, 2),
            "load_avg_5m": round(load5, 2),
            "load_avg_15m": round(load15, 2),
            "core_count": os.cpu_count() or 1,
        }
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------------


def _collect_memory() -> dict[str, Any] | None:
    try:
        if sys.platform == "linux":
            return _memory_linux()
        if sys.platform == "darwin":
            return _memory_darwin()
    except Exception:  # noqa: BLE001
        return None
    return None


def _memory_linux() -> dict[str, Any] | None:
    info: dict[str, int] = {}
    with open("/proc/meminfo") as fh:
        for line in fh:
            parts = line.split()
            if len(parts) >= 2:
                key = parts[0].rstrip(":")
                info[key] = int(parts[1])  # value in kB

    total_kb = info.get("MemTotal", 0)
    available_kb = info.get("MemAvailable", 0)
    if total_kb == 0:
        return None

    total_gb = round(total_kb / (1024**2), 1)
    available_gb = round(available_kb / (1024**2), 1)
    used_gb = round((total_kb - available_kb) / (1024**2), 1)
    percent = round((total_kb - available_kb) / total_kb * 100, 1)

    return {
        "total_gb": total_gb,
        "available_gb": available_gb,
        "used_gb": used_gb,
        "percent": percent,
    }


def _memory_darwin() -> dict[str, Any] | None:
    total_bytes = int(
        subprocess.check_output(  # noqa: S603, S607
            ["sysctl", "-n", "hw.memsize"], text=True
        ).strip()
    )
    user_bytes = int(
        subprocess.check_output(  # noqa: S603, S607
            ["sysctl", "-n", "hw.usermem"], text=True
        ).strip()
    )
    total_gb = round(total_bytes / (1024**3), 1)
    available_gb = round(user_bytes / (1024**3), 1)
    used_gb = round((total_bytes - user_bytes) / (1024**3), 1)
    percent = round((total_bytes - user_bytes) / total_bytes * 100, 1)

    return {
        "total_gb": total_gb,
        "available_gb": available_gb,
        "used_gb": used_gb,
        "percent": percent,
    }


# ---------------------------------------------------------------------------
# Disk
# ---------------------------------------------------------------------------


def _collect_disk() -> dict[str, Any] | None:
    try:
        usage = shutil.disk_usage("/")
        total_gb = round(usage.total / (1024**3), 1)
        used_gb = round(usage.used / (1024**3), 1)
        free_gb = round(usage.free / (1024**3), 1)
        percent = round(usage.used / usage.total * 100, 1) if usage.total else 0.0
        return {
            "total_gb": total_gb,
            "used_gb": used_gb,
            "free_gb": free_gb,
            "percent": percent,
        }
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Uptime
# ---------------------------------------------------------------------------


def _collect_uptime() -> dict[str, Any] | None:
    try:
        if sys.platform == "linux":
            return _uptime_linux()
        if sys.platform == "darwin":
            return _uptime_darwin()
    except Exception:  # noqa: BLE001
        return None
    return None


def _uptime_linux() -> dict[str, Any] | None:
    raw = Path("/proc/uptime").read_text().strip()
    seconds = int(float(raw.split()[0]))
    return {"seconds": seconds, "human": _humanize_seconds(seconds)}


def _uptime_darwin() -> dict[str, Any] | None:
    raw = subprocess.check_output(  # noqa: S603, S607
        ["sysctl", "-n", "kern.boottime"], text=True
    ).strip()
    # Format: "{ sec = 1712345678, usec = 123456 } ..."
    sec_part = raw.split("sec =")[1].split(",")[0].strip()
    boot_ts = int(sec_part)
    seconds = int(time.time() - boot_ts)
    return {"seconds": seconds, "human": _humanize_seconds(seconds)}


def _humanize_seconds(total: int) -> str:
    days, remainder = divmod(total, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, _ = divmod(remainder, 60)
    parts: list[str] = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes or not parts:
        parts.append(f"{minutes}m")
    return " ".join(parts)


# ---------------------------------------------------------------------------
# Platform
# ---------------------------------------------------------------------------


def _collect_platform() -> dict[str, Any]:
    return {
        "os": platform.platform(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "hostname": socket.gethostname(),
    }


# ---------------------------------------------------------------------------
# Server process
# ---------------------------------------------------------------------------


def _collect_process() -> dict[str, Any] | None:
    if _resource is None:
        return None

    getrusage = getattr(_resource, "getrusage", None)
    rusage_self = getattr(_resource, "RUSAGE_SELF", None)
    if getrusage is None or rusage_self is None:
        return None

    try:
        usage = getrusage(rusage_self)
        # maxrss is in kB on Linux, bytes on macOS
        rss_kb = usage.ru_maxrss if sys.platform == "linux" else usage.ru_maxrss // 1024
        result: dict[str, Any] = {"rss_mb": round(rss_kb / 1024, 1)}

        fd_dir = Path("/proc/self/fd")
        if fd_dir.is_dir():
            result["open_fds"] = len(list(fd_dir.iterdir()))

        return result
    except Exception:  # noqa: BLE001
        return None
