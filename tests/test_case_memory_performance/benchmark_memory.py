#!/usr/bin/env python3
"""
Comprehensive memory speedup benchmark.

Runs multiple iterations to get robust statistics on speedup.
"""

import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean, stdev

from app.main import _run
from tests.test_case_upstream_prefect_ecs_fargate.test_agent_e2e import (
    CONFIG,
    get_failure_details,
)
from tests.utils.alert_factory import create_alert

project_root = Path(__file__).parent.parent.parent


def run_single_investigation(enable_memory: bool, alert: dict) -> tuple[float, dict]:
    """Run a single investigation and return timing + result."""
    # Configure memory
    if enable_memory:
        os.environ["TRACER_MEMORY_ENABLED"] = "1"
    else:
        os.environ.pop("TRACER_MEMORY_ENABLED", None)

    # Time the investigation
    t1 = time.perf_counter()
    result = _run(
        alert_name=alert.get("labels", {}).get("alertname", "PrefectFlowFailure"),
        pipeline_name="upstream_downstream_pipeline_prefect",
        severity="critical",
        raw_alert=alert,
    )
    elapsed = time.perf_counter() - t1

    return elapsed, result


def main():
    print("=" * 70)
    print("MEMORY SPEEDUP COMPREHENSIVE BENCHMARK")
    print("=" * 70)

    # Get failure details
    try:
        failure_data = get_failure_details()
        if not failure_data:
            print("❌ No Prefect failure data available - infrastructure not deployed")
            return 1
    except Exception as e:
        print(f"❌ Could not get failure data: {e}")
        return 1

    # Create alert
    alert = create_alert(
        pipeline_name="upstream_downstream_pipeline_prefect",
        run_name=failure_data["flow_run_name"],
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        alert_name=f"Prefect Flow Failed: {failure_data['flow_run_name']}",
        annotations={
            "cloudwatch_log_group": failure_data["log_group"],
            "flow_run_id": failure_data["flow_run_id"],
            "flow_run_name": failure_data["flow_run_name"],
            "prefect_flow": "upstream_downstream_pipeline",
            "ecs_cluster": "tracer-prefect-cluster",
            "landing_bucket": failure_data["s3_bucket"],
            "s3_key": failure_data["s3_key"],
            "audit_key": failure_data["audit_key"],
            "prefect_api_url": CONFIG["prefect_api_url"],
            "error_message": failure_data["error_message"],
        },
    )

    # Clean memories
    memories_dir = project_root / "app" / "memories"
    for f in memories_dir.glob("*-upstream_downstream_pipeline_prefect-*.md"):
        if "IMPLEMENTATION" not in f.name and "FINDINGS" not in f.name and "SUCCESS" not in f.name and "SPEEDUP" not in f.name:
            f.unlink()
    print("✓ Cleaned prior memories\n")

    # Baseline runs (no memory, Sonnet)
    print("-" * 70)
    print("BASELINE RUNS (No Memory, Claude Sonnet)")
    print("-" * 70)

    baseline_times = []
    baseline_results = []
    n_baseline = 3

    for i in range(n_baseline):
        print(f"\nRun {i+1}/{n_baseline}...")
        elapsed, result = run_single_investigation(enable_memory=False, alert=alert)
        baseline_times.append(elapsed)
        baseline_results.append(result)
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Confidence: {result.get('confidence', 0):.0%}")
        print(f"  Validity: {result.get('validity_score', 0):.0%}")

    # Memory runs (with memory, Haiku)
    print("\n" + "-" * 70)
    print("MEMORY RUNS (With Memory, Claude Haiku + Guidance)")
    print("-" * 70)

    memory_times = []
    memory_results = []
    n_memory = 3

    for i in range(n_memory):
        print(f"\nRun {i+1}/{n_memory}...")
        elapsed, result = run_single_investigation(enable_memory=True, alert=alert)
        memory_times.append(elapsed)
        memory_results.append(result)
        print(f"  Time: {elapsed:.2f}s")
        print(f"  Confidence: {result.get('confidence', 0):.0%}")
        print(f"  Validity: {result.get('validity_score', 0):.0%}")

    # Statistical analysis
    print("\n" + "=" * 70)
    print("STATISTICAL ANALYSIS")
    print("=" * 70)

    baseline_mean = mean(baseline_times)
    baseline_std = stdev(baseline_times) if len(baseline_times) > 1 else 0
    memory_mean = mean(memory_times)
    memory_std = stdev(memory_times) if len(memory_times) > 1 else 0

    speedup_pct = ((baseline_mean - memory_mean) / baseline_mean) * 100
    speedup_seconds = baseline_mean - memory_mean

    print(f"\nBaseline (Sonnet, n={n_baseline}):")
    print(f"  Mean: {baseline_mean:.2f}s ± {baseline_std:.2f}s")
    print(f"  Range: {min(baseline_times):.2f}s - {max(baseline_times):.2f}s")

    print(f"\nWith Memory (Haiku, n={n_memory}):")
    print(f"  Mean: {memory_mean:.2f}s ± {memory_std:.2f}s")
    print(f"  Range: {min(memory_times):.2f}s - {max(memory_times):.2f}s")

    print("\nSpeedup:")
    print(f"  Absolute: {speedup_seconds:.2f}s faster")
    print(f"  Relative: {speedup_pct:.1f}% speedup")
    print(f"  Factor: {baseline_mean/memory_mean:.2f}x")

    # Quality comparison
    baseline_conf = mean([r.get("confidence", 0) for r in baseline_results])
    baseline_val = mean([r.get("validity_score", 0) for r in baseline_results])
    memory_conf = mean([r.get("confidence", 0) for r in memory_results])
    memory_val = mean([r.get("validity_score", 0) for r in memory_results])

    print("\nQuality Metrics:")
    print(f"  Baseline Confidence: {baseline_conf:.0%}")
    print(f"  Memory Confidence: {memory_conf:.0%} (Δ {memory_conf-baseline_conf:+.0%})")
    print(f"  Baseline Validity: {baseline_val:.0%}")
    print(f"  Memory Validity: {memory_val:.0%} (Δ {memory_val-baseline_val:+.0%})")

    # Pass/fail
    threshold = baseline_mean * 0.5
    print(f"\n50% Threshold: {threshold:.2f}s")
    print(f"Memory Result: {memory_mean:.2f}s")

    if memory_mean <= threshold:
        print(f"\n✅ PASS: {speedup_pct:.1f}% speedup (≥50% required)")
        return 0
    else:
        print(f"\n❌ FAIL: {speedup_pct:.1f}% speedup (<50% required)")
        return 1


if __name__ == "__main__":
    sys.exit(main())
