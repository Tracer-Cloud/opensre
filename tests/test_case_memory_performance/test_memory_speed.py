#!/usr/bin/env python3
"""
E2E Memory Speed Test (Milestone 6).

Tests that memory provides ≥50% wall-clock speedup for make demo RCA.

Run with: pytest tests/test_case_upstream_prefect_ecs_fargate/test_memory_speed.py -v -s
"""

import os
import shutil
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import pytest

from app.main import _run
from tests.test_case_upstream_prefect_ecs_fargate.test_agent_e2e import (
    CONFIG,
    get_failure_details,
)
from tests.utils.alert_factory import create_alert


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

    # Get failure details from Prefect
    try:
        failure_data = get_failure_details()
        if not failure_data:
            pytest.skip("No Prefect failure data available - infrastructure not deployed")
    except Exception as e:
        pytest.skip(f"Could not get failure data: {e}")

    # Create alert (same as test_agent_e2e.py)
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

    # ========== Baseline Run (No Memory) ==========
    print("\n" + "-" * 60)
    print("BASELINE RUN (No Memory)")
    print("-" * 60)

    # Clean memories folder
    memories_dir = project_root / "app" / "memories"
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

    threshold_time = baseline_time * 0.5
    print(f"\n50% Threshold: {threshold_time:.2f}s")
    print(f"Result:        {memory_time:.2f}s")

    if memory_time <= threshold_time:
        print(f"\n✅ PASS: {speedup_percent:.1f}% speedup (≥50% required)")
    else:
        print(f"\n❌ FAIL: {speedup_percent:.1f}% speedup (<50% required)")

    # Cleanup
    os.environ.pop("TRACER_MEMORY_ENABLED", None)

    # Assert 50% speedup requirement
    assert (
        memory_time <= threshold_time
    ), f"Memory speedup ({speedup_percent:.1f}%) did not meet 50% threshold"
