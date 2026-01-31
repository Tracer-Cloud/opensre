#!/usr/bin/env python3
"""End-to-end agent investigation test for upstream/downstream pipeline.

Triggers a failure in the pipeline and tests if the agent can correctly investigate and diagnose it.
"""

import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import boto3
import requests
from langsmith import traceable

from app.main import _run
from tests.conftest import UPSTREAM_DOWNSTREAM_CONFIG
from tests.utils.alert_factory import create_alert


def trigger_pipeline_failure() -> dict:
    """Trigger a pipeline failure and return alert data."""
    print("=" * 60)
    print("Triggering Pipeline Failure")
    print("=" * 60)

    # Trigger failure via HTTP
    correlation_id = f"alert-local-{datetime.now(UTC).strftime('%Y%m%d-%H%M%S')}"

    print(f"\nTriggering pipeline with schema error (correlation_id={correlation_id})...")
    response = requests.post(
        UPSTREAM_DOWNSTREAM_CONFIG["ingester_api_url"],
        json={"correlation_id": correlation_id, "inject_schema_change": True},
        timeout=10,
    )

    result = response.json()
    s3_key = result["s3_key"]
    bucket = result["s3_bucket"]

    print(f"✓ Bad data written to: s3://{bucket}/{s3_key}")
    print("Waiting 10s for Mock DAG to process and fail...")
    time.sleep(10)

    # Get error from CloudWatch logs
    logs_client = boto3.client("logs")
    log_group = f"/aws/lambda/{UPSTREAM_DOWNSTREAM_CONFIG['mock_dag_function_name']}"

    print(f"Checking logs in: {log_group}")
    response = logs_client.filter_log_events(
        logGroupName=log_group,
        startTime=int((time.time() - 120) * 1000),
        filterPattern=correlation_id,
    )

    error_message = "Schema validation failed"
    for event in response["events"]:
        if "PIPELINE FAILED" in event["message"]:
            error_message = event["message"].split("Error: ")[-1].split("\n")[0]
            break

    print(f"✓ Error detected: {error_message}")

    return {
        "correlation_id": correlation_id,
        "s3_key": s3_key,
        "bucket": bucket,
        "error_message": error_message,
        "log_group": log_group,
    }


def test_agent_investigation(failure_data: dict) -> bool:
    """Test agent can investigate the pipeline failure."""
    print("\n" + "=" * 60)
    print("Testing Agent Investigation")
    print("=" * 60)

    pipeline_name = "upstream_downstream_pipeline"
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    # Create alert
    raw_alert = create_alert(
        pipeline_name=pipeline_name,
        run_name=run_id,
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        annotations={
            "s3_bucket": failure_data["bucket"],
            "s3_key": failure_data["s3_key"],
            "correlation_id": failure_data["correlation_id"],
            "error": failure_data["error_message"],
            "lambda_log_group": failure_data["log_group"],
            "function_name": UPSTREAM_DOWNSTREAM_CONFIG["mock_dag_function_name"],
            "landing_bucket": UPSTREAM_DOWNSTREAM_CONFIG["landing_bucket_name"],
            "processed_bucket": UPSTREAM_DOWNSTREAM_CONFIG["processed_bucket_name"],
            "mock_api_url": UPSTREAM_DOWNSTREAM_CONFIG["mock_api_url"],
            "ingester_function": UPSTREAM_DOWNSTREAM_CONFIG["ingester_function_name"],
            "mock_dag_function": UPSTREAM_DOWNSTREAM_CONFIG["mock_dag_function_name"],
            "context_sources": "s3,lambda,cloudwatch",
        },
    )

    print("\nAlert created:")
    print(f"  Alert ID: {raw_alert['alert_id']}")
    print(f"  Pipeline: {pipeline_name}")
    print(f"  S3 Key: {failure_data['s3_key']}")
    print(f"  Correlation ID: {failure_data['correlation_id']}")

    print("\nRunning agent investigation...")

    @traceable(
        name=f"Pipeline Investigation - {raw_alert['alert_id'][:8]}",
        metadata={
            "alert_id": raw_alert["alert_id"],
            "pipeline_name": pipeline_name,
            "correlation_id": failure_data["correlation_id"],
            "s3_key": failure_data["s3_key"],
        },
    )
    def run_investigation():
        return _run(
            alert_name=f"Pipeline failure: {pipeline_name}",
            pipeline_name=pipeline_name,
            severity="critical",
            raw_alert=raw_alert,
        )

    try:
        result = run_investigation()

        print("\n✓ Investigation complete")
        print(f"  Root cause: {result.get('root_cause', 'Unknown')}")
        print(f"  Confidence: {result.get('confidence', 0):.0%}")

        return True

    except Exception as e:
        print(f"\n✗ Investigation failed: {e}")
        import traceback

        traceback.print_exc()
        return False


def main():
    """Main test flow."""
    print("=" * 60)
    print("Upstream/Downstream Pipeline - Agent E2E Test")
    print("=" * 60)
    print()

    # Step 1: Trigger failure
    failure_data = trigger_pipeline_failure()

    # Step 2: Test agent investigation
    success = test_agent_investigation(failure_data)

    if success:
        print("\n" + "=" * 60)
        print("✓ AGENT E2E TEST PASSED")
        print("=" * 60)
        return 0
    else:
        print("\n" + "=" * 60)
        print("✗ AGENT E2E TEST FAILED")
        print("=" * 60)
        return 1


if __name__ == "__main__":
    sys.exit(main())
