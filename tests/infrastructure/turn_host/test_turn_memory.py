"""Turn-end memory readings used to size turn concurrency."""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

from infrastructure.turn_host import turn_memory


def test_resident_memory_bytes_is_a_positive_measurement_or_none() -> None:
    # Contract: a real byte count where the platform exposes it, else None —
    # never zero, negative, or a raised error the turn host would have to catch.
    memory = turn_memory.resident_memory_bytes()
    assert memory is None or memory > 0


def test_peak_resident_memory_bytes_is_a_positive_measurement_or_none() -> None:
    peak = turn_memory.peak_resident_memory_bytes()
    assert peak is None or peak > 0


def test_import_and_sampling_succeed_without_resource_module() -> None:
    script = """
import sys

sys.modules["resource"] = None

from infrastructure.turn_host.turn_memory import (
    peak_resident_memory_bytes,
    resident_memory_bytes,
)

assert resident_memory_bytes() is None
assert peak_resident_memory_bytes() is None
"""
    subprocess.run([sys.executable, "-c", script], check=True)


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="ascii")


def test_container_memory_reads_cgroup_v2_before_v1(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # Fargate platform versions differ in cgroup version; v1 must still report.
    monkeypatch.setattr(turn_memory, "_CGROUP_ROOT", tmp_path)
    _write(tmp_path / "memory" / "memory.usage_in_bytes", "300\n")
    _write(tmp_path / "memory" / "memory.max_usage_in_bytes", "400\n")
    logger = logging.getLogger("test.turn_memory")

    v1 = turn_memory.record_turn_memory(logger, rss_before=None)
    _write(tmp_path / "memory.current", "100\n")
    _write(tmp_path / "memory.peak", "200\n")
    v2 = turn_memory.record_turn_memory(logger, rss_before=None)

    assert (v1.container_bytes, v1.container_peak_bytes) == (300, 400)
    assert (v2.container_bytes, v2.container_peak_bytes) == (100, 200)


def test_unavailable_cgroup_yields_none_without_raising(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # macOS dev has no cgroup; an unreadable value must not fail the turn.
    monkeypatch.setattr(turn_memory, "_CGROUP_ROOT", tmp_path)
    _write(tmp_path / "memory.current", "not-a-number\n")

    memory = turn_memory.record_turn_memory(logging.getLogger("test.turn_memory"), None)

    assert memory.container_bytes is None
    assert memory.container_peak_bytes is None
    assert memory.process_rss_delta_bytes is None
