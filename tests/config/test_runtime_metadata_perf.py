"""Perf regression guards for session runtime metadata.

Each test times its target N times, uses the median (robust to jitter), and
asserts a generous upper bound calibrated at ~10-100x the measured Darwin
arm64 baseline so slow CI runners don't flake.

Baseline (Darwin arm64, Python 3.14.3, n=200; includes host OS + workspace
identity probes):
- session dict lookup         ~0.0001 ms
- importlib.metadata.version  ~0.56 ms
- build_runtime_metadata      ~1.7 ms (can approach ~6 ms under xdist load)
- render_static_runtime_facts ~0.003 ms
"""

from __future__ import annotations

import importlib.metadata
import statistics
import time
from collections.abc import Callable

import pytest

from config.runtime_metadata import build_runtime_metadata
from core.agent_harness.prompts.runtime_facts import render_static_runtime_facts

_RUNS = 200


def _time_median_ms(fn: Callable[[], object]) -> float:
    """Median wall-time per call in milliseconds. Warmup + N samples."""
    for _ in range(5):
        fn()
    samples = []
    for _ in range(_RUNS):
        t0 = time.perf_counter_ns()
        fn()
        samples.append((time.perf_counter_ns() - t0) / 1_000_000.0)
    return statistics.median(samples)


def test_session_dict_lookup_stays_under_10us() -> None:
    """The steady-state path: agent reads pre-built dict during prompt build.

    Baseline ~0.0001 ms; 100x buffer = 0.01 ms = 10 µs. Anything above suggests
    the field type changed (e.g. lazy accessor, computed property) — regression.
    """
    metadata = build_runtime_metadata()
    median_ms = _time_median_ms(lambda: metadata.get("opensre_version"))
    print(f"\n  session dict lookup: {median_ms * 1000:.2f} µs")
    assert median_ms < 0.01, f"regression: {median_ms} ms > 0.01 ms threshold"


def test_importlib_version_lookup_stays_under_5ms() -> None:
    """The fallback path used inside the sandbox when session inputs aren't wired.

    Baseline ~0.56 ms; ~10x buffer = 5 ms. If this breaches, importlib's package
    resolution has slowed (dependency count, entry-point registration, etc).
    """
    median_ms = _time_median_ms(lambda: importlib.metadata.version("opensre"))
    print(f"\n  importlib.metadata.version: {median_ms:.2f} ms")
    assert median_ms < 5.0, f"regression: {median_ms} ms > 5 ms threshold"


def test_build_runtime_metadata_stays_under_15ms() -> None:
    """The one-time bootstrap cost at session init / /new / /resume.

    Baseline ~1.7 ms; ~10x buffer = 15 ms (covers xdist/suite-load jitter that
    previously breached a 5 ms cap around ~6 ms). If this breaches, someone
    added an expensive call to build_runtime_metadata — undermines the
    "cheap to call per session" invariant.
    """
    median_ms = _time_median_ms(build_runtime_metadata)
    print(f"\n  build_runtime_metadata: {median_ms:.2f} ms")
    assert median_ms < 15.0, f"regression: {median_ms} ms > 15 ms threshold"


def test_static_runtime_facts_render_stays_under_1ms() -> None:
    """The per-turn cost of rendering the runtime facts into the LLM prompt.

    Baseline ~0.003 ms; ~300x buffer = 1 ms. This runs per prompt build so
    even sub-millisecond overhead matters.
    """
    metadata = build_runtime_metadata()
    median_ms = _time_median_ms(lambda: render_static_runtime_facts(metadata))
    print(f"\n  render_static_runtime_facts: {median_ms * 1000:.2f} µs")
    assert median_ms < 1.0, f"regression: {median_ms} ms > 1 ms threshold"


@pytest.mark.parametrize("_i", range(3))
def test_baseline_stability(_i: int) -> None:
    """Run the whole quartet 3x to smoke-test flakiness under jitter.

    If any of the individual tests flake more than 1 in 3 under CI jitter, the
    threshold is too tight and should be relaxed. All four asserts inline here.
    """
    metadata = build_runtime_metadata()

    dict_ms = _time_median_ms(lambda: metadata.get("opensre_version"))
    imp_ms = _time_median_ms(lambda: importlib.metadata.version("opensre"))
    build_ms = _time_median_ms(build_runtime_metadata)

    env_ms = _time_median_ms(lambda: render_static_runtime_facts(metadata))

    assert dict_ms < 0.01, f"dict {dict_ms} ms"
    assert imp_ms < 5.0, f"importlib {imp_ms} ms"
    assert build_ms < 15.0, f"build {build_ms} ms"
    assert env_ms < 1.0, f"env {env_ms} ms"
