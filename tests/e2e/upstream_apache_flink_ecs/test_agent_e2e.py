#!/usr/bin/env python3
"""End-to-end agent investigation test for Flink ECS pipeline.

Tests if the agent can trace a schema validation failure through:
1. Flink task logs (ECS CloudWatch)
2. S3 input data
3. S3 metadata/audit trail
4. Trigger Lambda
5. External Vendor API
"""

import json
import sys
import time
from datetime import UTC, datetime

import boto3
import requests
from langsmith import traceable

from app.cli.investigate import run_investigation_cli
from tests.shared.stack_config import get_flink_config
from tests.utils.alert_factory import create_alert

# Configuration loaded dynamically from CloudFormation
CONFIG = get_flink_config()


def trigger_pipeline_failure() -> dict:
    """Trigger the Flink pipeline with error injection."""
    print("=" * 60)
    print("Triggering Flink Pipeline Failure")
    print("=" * 60)

    if not CONFIG["trigger_api_url"]:
        print("ERROR: TRIGGER_API_URL not configured")
        return None

    # Trigger with error injection
    url = f"{CONFIG['trigger_api_url']}trigger?inject_error=true"
    print(f"\nPOST {url}")

    response = requests.post(url, timeout=60)
    if not response.ok:
        print(f"ERROR: Trigger failed with status {response.status_code}")
        return None

    result = response.json()
    print(f"Trigger response: {json.dumps(result, indent=2)}")

    correlation_id = result.get("correlation_id")
    s3_key = result.get("s3_key")
    audit_key = result.get("audit_key")
    task_arn = result.get("task_arn")

    print(f"\nCorrelation ID: {correlation_id}")
    print(f"S3 Key: {s3_key}")
    print(f"Task ARN: {task_arn}")

    # Wait for ECS task to complete (and fail)
    print("\nWaiting for ECS task to complete...")
    time.sleep(30)  # Give task time to start and fail

    return {
        "correlation_id": correlation_id,
        "s3_key": s3_key,
        "audit_key": audit_key,
        "task_arn": task_arn,
        "bucket": CONFIG["landing_bucket"],
    }


def get_failure_details(failure_data: dict) -> dict:
    """Get error details from CloudWatch logs."""
    print("\n" + "=" * 60)
    print("Retrieving Failure Details from CloudWatch")
    print("=" * 60)

    logs_client = boto3.client("logs", region_name="us-east-1")
    correlation_id = failure_data["correlation_id"]

    try:
        response = logs_client.filter_log_events(
            logGroupName=CONFIG["log_group"],
            startTime=int((time.time() - 3600) * 1000),  # Last hour
            filterPattern=correlation_id,
        )

        error_message = "Schema validation failed"
        for event in response.get("events", []):
            message = event["message"]
            if "[FLINK][ERROR]" in message and "Schema validation failed" in message:
                error_message = message.split("[FLINK][ERROR]")[-1].strip()
                break

        print(f"Found error in logs: {error_message}")
        failure_data["error_message"] = error_message
        failure_data["log_group"] = CONFIG["log_group"]

    except Exception as e:
        print(f"Warning: Could not fetch CloudWatch logs: {e}")
        failure_data["error_message"] = "Schema validation failed: Missing fields ['customer_id']"
        failure_data["log_group"] = CONFIG["log_group"]

    return failure_data


def test_agent_investigation(failure_data: dict):
    """Test agent can investigate the Flink pipeline failure."""
    print("\n" + "=" * 60)
    print("Running Agent Investigation")
    print("=" * 60)

    # Create alert with Flink task information
    alert = create_alert(
        pipeline_name="tracer_flink_ml_feature_pipeline",
        run_name=failure_data.get("task_arn", "flink-task"),
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        alert_name=f"Flink ML Task Failed: {failure_data['correlation_id']}",
        annotations={
            "cloudwatch_log_group": failure_data["log_group"],
            "ecs_cluster": CONFIG["ecs_cluster"],
            "task_arn": failure_data.get("task_arn", ""),
            "landing_bucket": failure_data["bucket"],
            "s3_key": failure_data["s3_key"],
            "audit_key": failure_data.get("audit_key", ""),
            "processed_bucket": CONFIG["processed_bucket"],
            "correlation_id": failure_data["correlation_id"],
            "error_message": failure_data["error_message"],
            "trigger_function": CONFIG["trigger_lambda"],
            "mock_api_function": CONFIG["mock_api_lambda"],
            "mock_api_url": CONFIG["mock_api_url"],
            "context_sources": "s3,cloudwatch,ecs,lambda",
        },
    )

    print("\nAlert created:")
    print(f"   Pipeline: {alert.get('labels', {}).get('alertname', 'unknown')}")
    print(f"   Correlation ID: {failure_data['correlation_id']}")
    print(f"   Log Group: {failure_data['log_group']}")
    print(f"   S3 Data: s3://{failure_data['bucket']}/{failure_data['s3_key']}")
    if failure_data.get("audit_key"):
        print(f"   S3 Audit: s3://{failure_data['bucket']}/{failure_data['audit_key']}")

    print("\nStarting investigation agent...")
    print("-" * 60)

    # Run investigation with traceable metadata
    @traceable(
        run_type="chain",
        name=f"test_flink_ml - {alert['alert_id'][:8]}",
        metadata={
            "alert_id": alert["alert_id"],
            "pipeline_name": "tracer_flink_ml_feature_pipeline",
            "correlation_id": failure_data["correlation_id"],
            "s3_key": failure_data["s3_key"],
            "ecs_cluster": CONFIG["ecs_cluster"],
            "log_group": failure_data["log_group"],
            "task_arn": failure_data.get("task_arn"),
        },
    )
    def run_investigation():
        return run_investigation_cli(
            alert_name=alert.get("labels", {}).get("alertname", "FlinkTaskFailure"),
            pipeline_name="tracer_flink_batch_pipeline",
            severity="critical",
            raw_alert=alert,
        )

    result = run_investigation()

    print("-" * 60)
    print("\nInvestigation Results:")
    print(f"   Status: {result.get('status', 'unknown')}")

    # Analyze investigation output
    investigation = result.get("investigation", {})
    root_cause = result.get("root_cause_analysis", {})

    print("\nInvestigation Summary:")
    if investigation:
        print(f"   Context gathered: {len(investigation)} items")
        for key, value in investigation.items():
            if isinstance(value, dict):
                print(f"   - {key}: {len(value)} entries")
            elif isinstance(value, list):
                print(f"   - {key}: {len(value)} items")

    print("\nRoot Cause Analysis:")
    if root_cause:
        print(json.dumps(root_cause, indent=2))

    # Check if agent identified the key components
    success_checks = {
        "Flink logs retrieved": False,
        "S3 input data inspected": False,
        "Audit trail traced": False,
        "External API identified": False,
        "Schema change detected": False,
    }

    investigation_text = json.dumps(result).lower()

    if "cloudwatch" in investigation_text or "flink" in investigation_text:
        success_checks["Flink logs retrieved"] = True

    if failure_data["s3_key"] in investigation_text or "ingested/" in investigation_text:
        success_checks["S3 input data inspected"] = True

    if failure_data.get("audit_key") and (
        failure_data["audit_key"] in investigation_text or "audit/" in investigation_text
    ):
        success_checks["Audit trail traced"] = True

    if "external" in investigation_text and (
        "api" in investigation_text or "vendor" in investigation_text
    ):
        success_checks["External API identified"] = True

    if "event_id" in investigation_text or "schema" in investigation_text:
        success_checks["Schema change detected"] = True

    print("\nSuccess Checks:")
    passed_count = 0
    for check, passed in success_checks.items():
        status = "PASS" if passed else "FAIL"
        print(f"   [{status}] {check}")
        if passed:
            passed_count += 1

    # Require at least 4/5 checks to pass (schema change detection is critical)
    min_required = 4
    if passed_count < min_required:
        print(f"\nFailed: Agent passed {passed_count}/{len(success_checks)} checks, need {min_required}")
        return False
    if not success_checks["Schema change detected"]:
        print("\nFailed: Agent must detect schema change as root cause")
        return False

    return True


def main():
    """Run the end-to-end test."""
    print("\n" + "=" * 60)
    print("FLINK ECS E2E INVESTIGATION TEST")
    print("=" * 60)

    # Trigger failure
    failure_data = trigger_pipeline_failure()
    if not failure_data:
        print("\nERROR: Could not trigger pipeline failure")
        return False

    # Get failure details from logs
    failure_data = get_failure_details(failure_data)

    # Run agent investigation
    success = test_agent_investigation(failure_data)

    print("\n" + "=" * 60)
    if success:
        print("TEST PASSED: Agent successfully traced the failure")
        print("   and detected the schema change as root cause")
    else:
        print("TEST FAILED: Agent could not complete full trace")
    print("=" * 60 + "\n")

    return success


if __name__ == "__main__":
    # Load configuration from environment or CDK outputs
    import os

    CONFIG["trigger_api_url"] = os.environ.get("TRIGGER_API_URL", CONFIG["trigger_api_url"])
    CONFIG["landing_bucket"] = os.environ.get("LANDING_BUCKET", CONFIG["landing_bucket"])
    CONFIG["processed_bucket"] = os.environ.get("PROCESSED_BUCKET", CONFIG["processed_bucket"])

    success = main()
    sys.exit(0 if success else 1)
