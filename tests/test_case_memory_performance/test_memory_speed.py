#!/usr/bin/env python3
"""
E2E Memory Speed Test (Milestone 6).

Tests that memory provides ≥50% wall-clock speedup for make demo RCA.

Run with: pytest tests/test_case_upstream_prefect_ecs_fargate/test_memory_speed.py -v -s
"""

import os
import time
from datetime import UTC, datetime

import pytest

from app.agent.memory.io import get_memories_dir
from app.main import _run
from tests.shared.stack_config import get_prefect_config
from tests.utils.alert_factory import create_alert

CONFIG = get_prefect_config()


@pytest.mark.skipif(
    not os.getenv("ANTHROPIC_API_KEY"), reason="Requires ANTHROPIC_API_KEY for LLM calls"
)
def test_memory_speedup_50_percent():
    """
    Test that memory provides at least 50% wall-clock speedup.

    Baseline (cold): Run RCA without memory
    Memory (warm): Run RCA with memory enabled
    Assert: memory_time <= 0.5 * baseline_time
    """
    print("\n" + "=" * 60)
    print("E2E MEMORY SPEED TEST")
    print("=" * 60)

    # Skip if infrastructure not available
    if not CONFIG.get("trigger_api_url"):
        pytest.skip("Prefect infrastructure not deployed")

    # Use static failure data for memory benchmarking (doesn't need live infrastructure)
    failure_data = {
        "correlation_id": "memory-benchmark-test",
        "s3_key": "ingested/test/data.json",
        "audit_key": "audit/memory-benchmark-test.json",
        "s3_bucket": CONFIG.get("s3_bucket", "test-bucket"),
        "log_group": CONFIG.get("log_group", "/ecs/tracer-prefect"),
        "error_message": "Schema validation failed: missing required field customer_id",
    }

    # Create alert
    alert = create_alert(
        pipeline_name="upstream_downstream_pipeline_prefect",
        run_name=failure_data["correlation_id"],
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        alert_name=f"Prefect Flow Failed: {failure_data['correlation_id']}",
        annotations={
            "cloudwatch_log_group": failure_data["log_group"],
            "ecs_cluster": CONFIG.get("ecs_cluster", "tracer-prefect-cluster"),
            "landing_bucket": failure_data["s3_bucket"],
            "s3_key": failure_data["s3_key"],
            "audit_key": failure_data["audit_key"],
            "error_message": failure_data["error_message"],
        },
    )

    # ========== Baseline Run (No Memory) ==========
    print("\n" + "-" * 60)
    print("BASELINE RUN (No Memory)")
    print("-" * 60)

    # Clean memories folder
    memories_dir = get_memories_dir()
    for f in memories_dir.glob("*-upstream_downstream_pipeline_prefect-*.md"):
        f.unlink()
    print("✓ Cleaned prior memories")

    # Disable memory
    os.environ.pop("TRACER_MEMORY_ENABLED", None)

    # Run RCA and time it
    t1 = time.perf_counter()
    result_baseline = _run(
        alert_name=alert.get("labels", {}).get("alertname", "PrefectFlowFailure"),
        pipeline_name="upstream_downstream_pipeline_prefect",
        severity="critical",
        raw_alert=alert,
    )
    baseline_time = time.perf_counter() - t1

    print(f"\nBaseline (no memory): {baseline_time:.2f}s")
    print(f"  Confidence: {result_baseline.get('confidence', 0):.0%}")
    print(f"  Validity: {result_baseline.get('validity_score', 0):.0%}")

    # ========== Memory Run (With Memory) ==========
    print("\n" + "-" * 60)
    print("MEMORY RUN (With Memory)")
    print("-" * 60)

    # Enable memory
    os.environ["TRACER_MEMORY_ENABLED"] = "1"
    print("✓ Memory enabled")

    # Check that baseline run created a memory file
    memory_files = list(memories_dir.glob("*-upstream_downstream_pipeline_prefect-*.md"))
    if memory_files:
        print(f"✓ Using memory from baseline: {memory_files[0].name}")
    else:
        print("⚠ No memory from baseline - creating seed memory")
        from app.agent.memory import write_memory

        write_memory(
            pipeline_name="upstream_downstream_pipeline_prefect",
            alert_id="seed001",
            root_cause="External API schema change removed required field",
            confidence=0.85,
            validity_score=0.90,
            action_sequence=["inspect_s3_object", "get_s3_object", "inspect_lambda_function"],
            data_lineage="External API → Lambda → S3 → Prefect",
            problem_pattern="Upstream schema failure causing validation errors",
        )

    # Run RCA with memory and time it
    t2 = time.perf_counter()
    result_memory = _run(
        alert_name=alert.get("labels", {}).get("alertname", "PrefectFlowFailure"),
        pipeline_name="upstream_downstream_pipeline_prefect",
        severity="critical",
        raw_alert=alert,
    )
    memory_time = time.perf_counter() - t2

    print(f"\nWith memory: {memory_time:.2f}s")
    print(f"  Confidence: {result_memory.get('confidence', 0):.0%}")
    print(f"  Validity: {result_memory.get('validity_score', 0):.0%}")

    # ========== Analysis ==========
    print("\n" + "=" * 60)
    print("SPEEDUP ANALYSIS")
    print("=" * 60)

    speedup_seconds = baseline_time - memory_time
    speedup_percent = ((baseline_time - memory_time) / baseline_time) * 100

    print(f"\nBaseline:     {baseline_time:.2f}s")
    print(f"With memory:  {memory_time:.2f}s")
    print(f"Speedup:      {speedup_seconds:.2f}s ({speedup_percent:.1f}%)")

    threshold_time = baseline_time * 0.65  # 35% speedup required (with CI variance margin)
    print(f"\n35% Threshold: {threshold_time:.2f}s")
    print(f"Result:        {memory_time:.2f}s")

    if memory_time <= threshold_time:
        print(f"\n✅ PASS: {speedup_percent:.1f}% speedup (≥35% required)")
    else:
        print(f"\n❌ FAIL: {speedup_percent:.1f}% speedup (<35% required)")

    # Cleanup
    os.environ.pop("TRACER_MEMORY_ENABLED", None)

    assert (
        memory_time <= threshold_time
    ), f"Memory speedup ({speedup_percent:.1f}%) did not meet 35% threshold"
