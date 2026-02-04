#!/usr/bin/env python3
"""End-to-end agent investigation test for Prefect ECS pipeline.

Tests if the agent can trace a schema validation failure through:
1. Prefect flow logs (ECS CloudWatch)
2. S3 input data
3. S3 metadata/audit trail
4. Trigger Lambda
5. External Vendor API
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
from tests.shared.grafana_helpers import build_grafana_loki_explore_url
from tests.shared.stack_config import get_prefect_config
from tests.utils.alert_factory import create_alert

# Configuration loaded dynamically from CloudFormation
CONFIG = get_prefect_config()


def trigger_pipeline_failure() -> dict:
    """Trigger the Prefect pipeline with error injection."""
    print("=" * 60)
    print("Triggering Prefect Pipeline Failure")
    print("=" * 60)

    url = f"{CONFIG['trigger_api_url']}trigger?inject_error=true"
    print(f"\nPOST {url}")

    response = requests.post(url, timeout=60)
    if not response.ok:
        print(f"ERROR: Trigger failed with status {response.status_code}")
        print(f"Response: {response.text}")
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
    time.sleep(30)

    return {
        "correlation_id": correlation_id,
        "s3_key": s3_key,
        "audit_key": audit_key,
        "task_arn": task_arn,
        "bucket": CONFIG["s3_bucket"],
    }


def get_failure_details_from_logs(trigger_data: dict) -> dict:
    """Get error details from CloudWatch logs."""
    print("=" * 60)
    print("Retrieving Failure Details from CloudWatch")
    print("=" * 60)

    logs_client = boto3.client("logs", region_name="us-east-1")

    try:
        response = logs_client.filter_log_events(
            logGroupName=CONFIG["log_group"],
            filterPattern="ERROR",
            limit=50,
        )

        error_message = "Schema validation failed"
        for event in response.get("events", []):
            msg = event.get("message", "")
            if "Schema validation failed" in msg or "required field" in msg.lower():
                error_message = msg[:200]
                print(f"Found error in logs: {error_message[:100]}")
                break

        return {
            **trigger_data,
            "error_message": error_message,
            "log_group": CONFIG["log_group"],
        }

    except Exception as e:
        print(f"Warning: Could not query logs: {e}")
        return {
            **trigger_data,
            "error_message": "Schema validation failed",
            "log_group": CONFIG["log_group"],
        }


def test_agent_investigation(failure_data: dict) -> bool:
    """Test agent can investigate the Prefect pipeline failure."""
    print("\n" + "=" * 60)
    print("Running Agent Investigation")
    print("=" * 60)

    alert = create_alert(
        pipeline_name="upstream_downstream_pipeline_prefect",
        run_name=failure_data["correlation_id"],
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        alert_name=f"Prefect Flow Failed: {failure_data['correlation_id']}",
        annotations={
            "cloudwatch_log_group": failure_data["log_group"],
            "ecs_cluster": CONFIG["ecs_cluster"],
            "task_arn": failure_data.get("task_arn", ""),
            "landing_bucket": failure_data["bucket"],
            "s3_key": failure_data["s3_key"],
            "audit_key": failure_data.get("audit_key", ""),
            "processed_bucket": CONFIG.get("processed_bucket", ""),
            "correlation_id": failure_data["correlation_id"],
            "error_message": failure_data["error_message"],
            "mock_api_url": CONFIG.get("mock_api_url", ""),
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

    @traceable(
        run_type="chain",
        name=f"test_prefect_ecs - {alert['alert_id'][:8]}",
        metadata={
            "alert_id": alert["alert_id"],
            "pipeline_name": "upstream_downstream_pipeline_prefect",
            "correlation_id": failure_data["correlation_id"],
            "ecs_cluster": CONFIG["ecs_cluster"],
            "log_group": failure_data["log_group"],
            "s3_key": failure_data["s3_key"],
        },
    )
    def run_investigation():
        return _run(
            alert_name=alert.get("labels", {}).get("alertname", "PrefectFlowFailure"),
            pipeline_name="upstream_downstream_pipeline_prefect",
            severity="critical",
            raw_alert=alert,
        )

    result = run_investigation()

    print("\n" + "=" * 60)
    print("RCA REPORT")
    print("=" * 60)
    print(result.get("report", "No report generated"))

    confidence = result.get("confidence", 0)
    validity = result.get("validity_score", 0)
    print("\n" + "=" * 60)
    print(f"Investigation complete. Confidence: {confidence}% | Validity: {validity}%")
    print("-" * 60)

    print("\nInvestigation Results:")
    print(f"   Status: {result.get('status', 'unknown')}")

    print("\nInvestigation Summary:")
    print(result.get("summary", "No summary available"))

    print("\nRoot Cause Analysis:")
    print(result.get("root_cause", "No root cause determined"))

    # Check success criteria
    success_checks = {
        "Prefect logs retrieved": False,
        "S3 input data inspected": False,
        "Audit trail traced": False,
        "External API identified": False,
        "Schema change detected": False,
    }

    investigation_text = json.dumps(result).lower()

    if "cloudwatch" in investigation_text or "prefect" in investigation_text:
        success_checks["Prefect logs retrieved"] = True

    s3_key = failure_data.get("s3_key", "")
    if s3_key in investigation_text or "ingested/" in investigation_text:
        success_checks["S3 input data inspected"] = True

    audit_key = failure_data.get("audit_key", "")
    if audit_key in investigation_text or "audit/" in investigation_text:
        success_checks["Audit trail traced"] = True

    if "external" in investigation_text and (
        "api" in investigation_text or "vendor" in investigation_text
    ):
        success_checks["External API identified"] = True

    if "customer_id" in investigation_text or "schema" in investigation_text:
        success_checks["Schema change detected"] = True

    print("\nSuccess Checks:")
    all_passed = True
    for check, passed in success_checks.items():
        status = "[PASS]" if passed else "[FAIL]"
        print(f"   {status} {check}")
        if not passed:
            all_passed = False

    return all_passed


def main():
    """Run the end-to-end test."""
    print("\n" + "=" * 60)
    print("PREFECT ECS E2E INVESTIGATION TEST")
    print("=" * 60)

    # Trigger a pipeline failure
    trigger_data = trigger_pipeline_failure()
    if not trigger_data:
        print("\nERROR: Could not trigger pipeline failure")
        return False

    # Get failure details from CloudWatch
    failure_data = get_failure_details_from_logs(trigger_data)

    # Run agent investigation
    success = test_agent_investigation(failure_data)

    print("\n" + "=" * 60)
    if success:
        print("TEST PASSED: Agent successfully traced the failure")
        print("   and detected the schema change as root cause")
    else:
        print("TEST FAILED: Agent did not detect all expected signals")
    print("=" * 60)

    log_url = build_grafana_loki_explore_url(
        service_name="prefect-etl-pipeline",
        correlation_id=failure_data.get("correlation_id"),
    )
    print("\nGrafana Cloud logs (Prefect flow service):")
    if log_url:
        print(f"  {log_url}")
    else:
        print("  (Grafana Cloud instance URL not configured)")
    print("  Paste this log URL after the test run.")

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
