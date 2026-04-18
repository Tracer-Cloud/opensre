"""Hardware detection for local LLM model recommendation."""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass

# Conservative fallback when RAM detection fails — leads recommend_model to pick the lightweight 3B model
_FALLBACK_RAM_GB = 8.0


@dataclass(frozen=True)
class HardwareProfile:
    total_ram_gb: float
    available_ram_gb: float
    arch: str
    is_apple_silicon: bool
    has_nvidia_gpu: bool


def detect_hardware() -> HardwareProfile:
    total = _get_total_ram_gb()
    available = _get_available_ram_gb(total)
    arch = platform.machine()
    is_apple_silicon = sys.platform == "darwin" and arch == "arm64"
    has_nvidia = shutil.which("nvidia-smi") is not None
    return HardwareProfile(
        total_ram_gb=total,
        available_ram_gb=available,
        arch=arch,
        is_apple_silicon=is_apple_silicon,
        has_nvidia_gpu=has_nvidia,
    )


def _get_total_ram_gb() -> float:
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.memsize"], text=True)
            return int(out.strip()) / (1024**3)
        elif sys.platform == "linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        return int(line.split()[1]) / (1024**2)
    except Exception:  # noqa: BLE001 — safe fallback: any detection failure uses conservative default
        return _FALLBACK_RAM_GB
    return _FALLBACK_RAM_GB


def _get_available_ram_gb(total_ram_gb: float) -> float:
    try:
        if sys.platform == "darwin":
            out = subprocess.check_output(["sysctl", "-n", "hw.usermem"], text=True)
            return int(out.strip()) / (1024**3)
        elif sys.platform == "linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable:"):
                        return int(line.split()[1]) / (1024**2)
    except Exception:  # noqa: BLE001 — safe fallback: any detection failure uses conservative default
        return total_ram_gb * 0.5
    return total_ram_gb * 0.5


def recommend_model(hw: HardwareProfile) -> tuple[str, str]:
    """Return (model_name, human_reason). Conservative — caps usable RAM at 50% of total."""
    safe_ram = min(hw.available_ram_gb, hw.total_ram_gb * 0.5)
    if hw.is_apple_silicon and hw.total_ram_gb >= 16 and safe_ram >= 6:
        return (
            "llama3.1:8b",
            f"{hw.total_ram_gb:.0f}GB Apple Silicon ({safe_ram:.0f}GB free) — unified memory handles 8B well",
        )
    if hw.has_nvidia_gpu or safe_ram >= 12:
        return "llama3.1:8b", f"{safe_ram:.0f}GB safely available — 8B model fits comfortably"
    return "llama3.2", f"{safe_ram:.0f}GB safely available — lightweight 3B for smooth performance"
