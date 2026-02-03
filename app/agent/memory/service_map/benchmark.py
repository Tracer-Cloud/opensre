"""Benchmark service map impact on investigation time.

Measures investigation time with service map ON vs OFF to validate performance claims.
"""

import time

from app.agent.graph_pipeline import run_investigation
from app.agent.memory.io import get_memories_dir


def _clean_service_map() -> None:
    """Remove service map file."""
    service_map_path = get_memories_dir() / "service_map.json"
    if service_map_path.exists():
        service_map_path.unlink()


def _clean_memory_files(pipeline_name: str) -> None:
    """Remove memory files for a pipeline."""
    memories_dir = get_memories_dir()
    if memories_dir.exists():
        for f in memories_dir.glob(f"*{pipeline_name}*.md"):
            if f.name not in ("IMPLEMENTATION_PLAN.md", "FINDINGS.md", "SUCCESS.md"):
                f.unlink()


def run_benchmark(
    alert_name: str,
    pipeline_name: str,
    severity: str,
    raw_alert: dict,
    runs: int = 3,
) -> tuple[float, float]:
    """Run investigation benchmark with service map ON vs OFF.

    Args:
        alert_name: Alert name
        pipeline_name: Pipeline name
        severity: Alert severity
        raw_alert: Raw alert payload
        runs: Number of runs per configuration

    Returns:
        Tuple of (avg_time_without_map, avg_time_with_map) in seconds
    """
    times_without = []
    times_with = []

    print(f"\n{'='*60}")
    print(f"BENCHMARK: {pipeline_name}")
    print(f"{'='*60}\n")

    # Phase 1: Run WITHOUT service map
    print("Phase 1: WITHOUT service map (cold start)")
    print("-" * 60)

    from . import config

    config.SERVICE_MAP_ENABLED = False

    for i in range(runs):
        _clean_service_map()
        _clean_memory_files(pipeline_name)

        start = time.time()
        run_investigation(
            alert_name=alert_name,
            pipeline_name=pipeline_name,
            severity=severity,
            raw_alert=raw_alert,
        )
        elapsed = time.time() - start

        times_without.append(elapsed)
        print(f"  Run {i+1}: {elapsed:.2f}s")

    avg_without = sum(times_without) / len(times_without)
    print(f"\nAverage WITHOUT service map: {avg_without:.2f}s")

    # Phase 2: Build service map with first run
    print("\nPhase 2: Building service map")
    print("-" * 60)

    config.SERVICE_MAP_ENABLED = True
    _clean_service_map()
    _clean_memory_files(pipeline_name)

    start = time.time()
    run_investigation(
        alert_name=alert_name,
        pipeline_name=pipeline_name,
        severity=severity,
        raw_alert=raw_alert,
    )
    build_time = time.time() - start
    print(f"  Initial build: {build_time:.2f}s (creates service map)")

    # Phase 3: Run WITH service map (warm start)
    print("\nPhase 3: WITH service map (warm start)")
    print("-" * 60)

    for i in range(runs):
        # Clean memory but keep service map
        _clean_memory_files(pipeline_name)

        start = time.time()
        run_investigation(
            alert_name=alert_name,
            pipeline_name=pipeline_name,
            severity=severity,
            raw_alert=raw_alert,
        )
        elapsed = time.time() - start

        times_with.append(elapsed)
        print(f"  Run {i+1}: {elapsed:.2f}s")

    avg_with = sum(times_with) / len(times_with)
    print(f"\nAverage WITH service map: {avg_with:.2f}s")

    # Results
    print(f"\n{'='*60}")
    print("RESULTS")
    print(f"{'='*60}")
    print(f"Average WITHOUT service map: {avg_without:.2f}s")
    print(f"Average WITH service map: {avg_with:.2f}s")
    print(f"Difference: {avg_without - avg_with:.2f}s")
    print(f"Improvement: {((avg_without - avg_with) / avg_without * 100):.1f}%")
    print(f"{'='*60}\n")

    # Restore config
    config.SERVICE_MAP_ENABLED = True

    return avg_without, avg_with


def run_multi_pipeline_benchmark() -> dict[str, tuple[float, float]]:
    """Run benchmark across multiple pipelines to measure cumulative benefit.

    Returns:
        Dict mapping pipeline name to (time_without, time_with) tuples
    """
    results: dict[str, tuple[float, float]] = {}

    # We'll need to import test alerts here
    # For now, return empty - will be populated by actual test runner

    return results


if __name__ == "__main__":
    print("\n" + "=" * 80)
    print("SERVICE MAP PERFORMANCE BENCHMARK")
    print("=" * 80)
    print("\nThis benchmark measures investigation time with service map ON vs OFF.")
    print("Run this from test files that have alert fixtures.")
    print("=" * 80)
