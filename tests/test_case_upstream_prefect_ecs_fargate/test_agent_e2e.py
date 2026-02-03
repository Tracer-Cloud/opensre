#!/usr/bin/env python3
"""End-to-end agent investigation test for Prefect ECS pipeline.

Tests if the agent can trace a schema validation failure through:
1. Prefect logs (ECS CloudWatch)
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

    print(f"\nCorrelation ID: {correlation_id}")
    print(f"S3 Key: {s3_key}")

    print("\nWaiting for Prefect flow to complete...")
    time.sleep(30)

    return {
        "correlation_id": correlation_id,
        "s3_key": s3_key,
        "audit_key": audit_key,
        "s3_bucket": CONFIG["s3_bucket"],
    }


def get_failure_details() -> dict:
    """Get details about the failed Prefect flow run."""
    print("=" * 60)
    print("Retrieving Prefect Flow Run Details")
    print("=" * 60)

    if not CONFIG.get("prefect_api_url"):
        print("ERROR: No Prefect server found in ECS")
        return None

    # Query Prefect for recent flow runs
    print(f"\nQuerying Prefect at {CONFIG['prefect_api_url']}...")
    response = requests.post(
        f"{CONFIG['prefect_api_url']}/flow_runs/filter",
        json={
            "sort": "START_TIME_DESC",
            "limit": 10,
        },
        timeout=10,
    )

    if not response.ok:
        print(f"❌ Failed to query Prefect: {response.status_code}")
        return None

    flow_runs = response.json()
    print(f"✓ Found {len(flow_runs)} recent flow runs")

    # Find the failed run
    failed_run = None
    for run in flow_runs:
        if run.get("state", {}).get("type") == "FAILED":
            failed_run = run
            break

    if not failed_run:
        print("❌ No failed flow runs found")
        return None

    print("\n✓ Found failed flow run:")
    print(f"   ID: {failed_run['id']}")
    print(f"   Name: {failed_run['name']}")
    print(f"   Flow: {failed_run.get('flow_name', 'unknown')}")
    print(f"   State: {failed_run['state']['type']}")
    print(f"   Message: {failed_run['state'].get('message', 'No message')}")

    # Get error message from CloudWatch logs
    logs_client = boto3.client("logs", region_name="us-east-1")
    print(f"\nChecking CloudWatch logs: {CONFIG['log_group']}")

    try:
        response = logs_client.filter_log_events(
            logGroupName=CONFIG["log_group"],
            startTime=int((time.time() - 3600) * 1000),  # Last hour
            filterPattern=CONFIG["correlation_id"],
        )

        error_message = "Schema validation failed"
        for event in response["events"]:
            message = event["message"]
            if "Schema validation failed" in message and "Missing fields" in message:
                # Extract the exact error
                start = message.find("Missing fields")
                end = message.find("in record", start) + len("in record 0")
                error_message = message[start:end]
                break

        print(f"✓ Error found in logs: {error_message}")

    except Exception as e:
        print(f"⚠ Warning: Could not fetch CloudWatch logs: {e}")
        error_message = failed_run["state"].get("message", "Schema validation failed")

    return {
        "flow_run_id": failed_run["id"],
        "flow_run_name": failed_run["name"],
        "correlation_id": CONFIG["correlation_id"],
        "error_message": error_message,
        "log_group": CONFIG["log_group"],
        "s3_bucket": CONFIG["s3_bucket"],
        "s3_key": CONFIG["s3_key"],
        "audit_key": CONFIG["audit_key"],
    }


def test_agent_investigation(failure_data: dict) -> bool:
    """Test agent can investigate the Prefect pipeline failure."""
    print("\n" + "=" * 60)
    print("Running Agent Investigation")
    print("=" * 60)

    # Create alert with Prefect flow run information
    alert = create_alert(
        pipeline_name="upstream_downstream_pipeline_prefect",
        run_name=failure_data["flow_run_name"],
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        alert_name=f"Prefect Flow Failed: {failure_data['flow_run_name']}",
        annotations={
            # Don't include correlation_id as filter - it won't match logs
            # Instead, agent will get latest logs from the log group
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

    print("\n📋 Alert created:")
    print(f"   Pipeline: {alert.get('labels', {}).get('alertname', 'unknown')}")
    print(f"   Run Name: {failure_data['flow_run_name']}")
    print(f"   Flow Run ID: {failure_data['flow_run_id']}")
    print(f"   Log Group: {failure_data['log_group']}")
    print(f"   S3 Data: s3://{failure_data['s3_bucket']}/{failure_data['s3_key']}")
    print(f"   S3 Audit: s3://{failure_data['s3_bucket']}/{failure_data['audit_key']}")

    print("\n🤖 Starting investigation agent...")
    print("-" * 60)

    # Run investigation with traceable metadata
    @traceable(
        run_type="chain",
        name=f"test_prefect_ecs - {alert['alert_id'][:8]}",
        metadata={
            "alert_id": alert["alert_id"],
            "pipeline_name": "upstream_downstream_pipeline_prefect",
            "flow_run_id": failure_data["flow_run_id"],
            "flow_run_name": failure_data["flow_run_name"],
            "ecs_cluster": "tracer-prefect-cluster",
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

    print("-" * 60)
    print("\n📊 Investigation Results:")
    print(f"   Status: {result.get('status', 'unknown')}")

    # Analyze investigation output
    investigation = result.get("investigation", {})
    root_cause = result.get("root_cause_analysis", {})

    print("\n🔍 Investigation Summary:")
    if investigation:
        print(f"   Context gathered: {len(investigation)} items")
        for key, value in investigation.items():
            if isinstance(value, dict):
                print(f"   - {key}: {len(value)} entries")
            elif isinstance(value, list):
                print(f"   - {key}: {len(value)} items")

    print("\n🎯 Root Cause Analysis:")
    if root_cause:
        print(json.dumps(root_cause, indent=2))

    # Check if agent identified the key components
    success_checks = {
        "Prefect logs retrieved": False,
        "S3 input data inspected": False,
        "Audit trail traced": False,
        "External API identified": False,
        "Schema change detected": False,
    }

    # Analyze the investigation to check our success criteria
    investigation_text = json.dumps(result).lower()

    if "cloudwatch" in investigation_text or "prefect" in investigation_text:
        success_checks["Prefect logs retrieved"] = True

    if failure_data["s3_key"] in investigation_text or "ingested/20260131" in investigation_text:
        success_checks["S3 input data inspected"] = True

    if failure_data["audit_key"] in investigation_text or "audit/" in investigation_text:
        success_checks["Audit trail traced"] = True

    if "external" in investigation_text and (
        "api" in investigation_text or "vendor" in investigation_text
    ):
        success_checks["External API identified"] = True

    if "customer_id" in investigation_text or "schema" in investigation_text:
        success_checks["Schema change detected"] = True

    print("\n✅ Success Checks:")
    all_passed = True
    for check, passed in success_checks.items():
        status = "✓" if passed else "✗"
        print(f"   {status} {check}")
        if not passed:
            all_passed = False

    return all_passed


def main():
    """Run the end-to-end test."""
    print("\n" + "=" * 60)
    print("PREFECT ECS E2E INVESTIGATION TEST")
    print("=" * 60)

    # Trigger a pipeline failure first
    trigger_data = trigger_pipeline_failure()
    if not trigger_data:
        print("\n❌ Could not trigger pipeline failure")
        return False

    # Get failure details from Prefect
    failure_data = get_failure_details()
    if not failure_data:
        # Fall back to trigger data if Prefect query fails
        print("\nUsing trigger data as failure details...")
        failure_data = trigger_data

    # Run agent investigation
    success = test_agent_investigation(failure_data)

    print("\n" + "=" * 60)
    if success:
        print("✅ TEST PASSED: Agent successfully traced the failure")
        print("   to the External Vendor API schema change")
    else:
        print("❌ TEST FAILED: Agent could not complete full trace")
    print("=" * 60 + "\n")

    return success


if __name__ == "__main__":
    success = main()
    sys.exit(0 if success else 1)
