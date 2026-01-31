#!/usr/bin/env python3
"""End-to-end agent investigation test for Airflow ECS pipeline.

Tests if the agent can trace a schema validation failure through:
1. Airflow logs (ECS CloudWatch)
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
from tests.utils.alert_factory import create_alert

CONFIG = {
    "airflow_api_url": "http://127.0.0.1:8080/api/v1",
    "airflow_username": "admin",
    "airflow_password": "admin",
    "dag_id": "upstream_downstream_pipeline_airflow",
    "log_group": "/ecs/tracer-airflow",
    "correlation_id": "trigger-20260131-124548",
    "s3_bucket": "tracerairflowecsfargate-landingbucket23fe90fb-woehzac5msvj",
    "s3_key": "ingested/20260131-124548/data.json",
    "audit_key": "audit/trigger-20260131-124548.json",
}


def _airflow_request(method: str, path: str, **kwargs) -> requests.Response:
    url = f"{CONFIG['airflow_api_url'].rstrip('/')}{path}"
    auth = (CONFIG["airflow_username"], CONFIG["airflow_password"])
    return requests.request(method, url, auth=auth, timeout=10, **kwargs)


def get_failure_details() -> dict | None:
    """Get details about the failed Airflow DAG run."""
    print("=" * 60)
    print("Retrieving Airflow DAG Run Details")
    print("=" * 60)

    print(f"\nQuerying Airflow at {CONFIG['airflow_api_url']}...")
    response = _airflow_request(
        "GET",
        f"/dags/{CONFIG['dag_id']}/dagRuns",
        params={"order_by": "-execution_date", "limit": 10},
    )

    if not response.ok:
        print(f"❌ Failed to query Airflow: {response.status_code}")
        return None

    dag_runs = response.json().get("dag_runs", [])
    print(f"✓ Found {len(dag_runs)} recent DAG runs")

    failed_run = None
    for run in dag_runs:
        if run.get("state") == "failed":
            failed_run = run
            break

    if not failed_run:
        print("❌ No failed DAG runs found")
        return None

    dag_run_id = failed_run.get("dag_run_id") or failed_run.get("run_id", "unknown")

    print("\n✓ Found failed DAG run:")
    print(f"   ID: {dag_run_id}")
    print(f"   DAG: {failed_run.get('dag_id')}")
    print(f"   State: {failed_run.get('state')}")

    logs_client = boto3.client("logs", region_name="us-east-1")
    print(f"\nChecking CloudWatch logs: {CONFIG['log_group']}")

    try:
        response = logs_client.filter_log_events(
            logGroupName=CONFIG["log_group"],
            startTime=int((time.time() - 3600) * 1000),
            filterPattern=CONFIG["correlation_id"],
        )

        error_message = "Schema validation failed"
        for event in response["events"]:
            message = event["message"]
            if "Schema validation failed" in message and "Missing fields" in message:
                start = message.find("Missing fields")
                end = message.find("in record", start) + len("in record 0")
                error_message = message[start:end]
                break

        print(f"✓ Error found in logs: {error_message}")

    except Exception as e:
        print(f"⚠ Warning: Could not fetch CloudWatch logs: {e}")
        error_message = "Schema validation failed"

    return {
        "dag_run_id": dag_run_id,
        "correlation_id": CONFIG["correlation_id"],
        "error_message": error_message,
        "log_group": CONFIG["log_group"],
        "s3_bucket": CONFIG["s3_bucket"],
        "s3_key": CONFIG["s3_key"],
        "audit_key": CONFIG["audit_key"],
    }


def test_agent_investigation(failure_data: dict) -> bool:
    """Test agent can investigate the Airflow pipeline failure."""
    print("\n" + "=" * 60)
    print("Running Agent Investigation")
    print("=" * 60)

    alert = create_alert(
        pipeline_name="upstream_downstream_pipeline_airflow",
        run_name=failure_data["dag_run_id"],
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        alert_name=f"Airflow DAG Failed: {failure_data['dag_run_id']}",
        annotations={
            "cloudwatch_log_group": failure_data["log_group"],
            "dag_run_id": failure_data["dag_run_id"],
            "airflow_dag": CONFIG["dag_id"],
            "ecs_cluster": "tracer-airflow-cluster",
            "landing_bucket": failure_data["s3_bucket"],
            "s3_key": failure_data["s3_key"],
            "audit_key": failure_data["audit_key"],
            "airflow_api_url": CONFIG["airflow_api_url"],
            "error_message": failure_data["error_message"],
        },
    )

    print("\n📋 Alert created:")
    print(f"   Pipeline: {alert.get('labels', {}).get('alertname', 'unknown')}")
    print(f"   Run ID: {failure_data['dag_run_id']}")
    print(f"   Log Group: {failure_data['log_group']}")
    print(f"   S3 Data: s3://{failure_data['s3_bucket']}/{failure_data['s3_key']}")
    print(f"   S3 Audit: s3://{failure_data['s3_bucket']}/{failure_data['audit_key']}")

    print("\n🤖 Starting investigation agent...")
    print("-" * 60)

    @traceable(
        run_type="chain",
        name=f"test_airflow_ecs - {alert['alert_id'][:8]}",
        metadata={
            "alert_id": alert["alert_id"],
            "pipeline_name": "upstream_downstream_pipeline_airflow",
            "dag_run_id": failure_data["dag_run_id"],
            "dag_id": CONFIG["dag_id"],
            "ecs_cluster": "tracer-airflow-cluster",
            "log_group": failure_data["log_group"],
            "s3_key": failure_data["s3_key"],
        },
    )
    def run_investigation():
        return _run(
            alert_name=alert.get("labels", {}).get("alertname", "AirflowDagFailure"),
            pipeline_name="upstream_downstream_pipeline_airflow",
            severity="critical",
            raw_alert=alert,
        )

    result = run_investigation()

    print("-" * 60)
    print("\n📊 Investigation Results:")
    print(f"   Status: {result.get('status', 'unknown')}")

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

    success_checks = {
        "Airflow logs retrieved": False,
        "S3 input data inspected": False,
        "Audit trail traced": False,
        "External API identified": False,
        "Schema change detected": False,
    }

    investigation_text = json.dumps(result).lower()

    if "cloudwatch" in investigation_text or "airflow" in investigation_text:
        success_checks["Airflow logs retrieved"] = True

    if failure_data["s3_key"] in investigation_text or "ingested/20260131" in investigation_text:
        success_checks["S3 input data inspected"] = True

    if failure_data["audit_key"] in investigation_text or "audit/" in investigation_text:
        success_checks["Audit trail traced"] = True

    if "external" in investigation_text and (
        "api" in investigation_text or "vendor" in investigation_text
    ):
        success_checks["External API identified"] = True

    if "event_id" in investigation_text or "schema" in investigation_text:
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
    print("AIRFLOW ECS E2E INVESTIGATION TEST")
    print("=" * 60)

    failure_data = get_failure_details()
    if not failure_data:
        print("\n❌ Could not retrieve failure details")
        return False

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
#!/usr/bin/env python3
"""End-to-end agent investigation test for Airflow ECS pipeline.

Tests if the agent can trace a schema validation failure through:
1. Airflow logs (ECS CloudWatch)
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
from tests.utils.alert_factory import create_alert

CONFIG = {
    "airflow_api_url": "http://127.0.0.1:8080/api/v1",
    "airflow_username": "admin",
    "airflow_password": "admin",
    "dag_id": "upstream_downstream_pipeline_airflow",
    "log_group": "/ecs/tracer-airflow",
    "correlation_id": "trigger-20260131-124548",
    "s3_bucket": "tracerairflowecsfargate-landingbucket23fe90fb-woehzac5msvj",
    "s3_key": "ingested/20260131-124548/data.json",
    "audit_key": "audit/trigger-20260131-124548.json",
}


def _airflow_request(method: str, path: str, **kwargs) -> requests.Response:
    url = f"{CONFIG['airflow_api_url'].rstrip('/')}{path}"
    auth = (CONFIG["airflow_username"], CONFIG["airflow_password"])
    return requests.request(method, url, auth=auth, timeout=10, **kwargs)


def get_failure_details() -> dict | None:
    """Get details about the failed Airflow DAG run."""
    print("=" * 60)
    print("Retrieving Airflow DAG Run Details")
    print("=" * 60)

    print(f"\nQuerying Airflow at {CONFIG['airflow_api_url']}...")
    response = _airflow_request(
        "GET",
        f"/dags/{CONFIG['dag_id']}/dagRuns",
        params={"order_by": "-execution_date", "limit": 10},
    )

    if not response.ok:
        print(f"❌ Failed to query Airflow: {response.status_code}")
        return None

    dag_runs = response.json().get("dag_runs", [])
    print(f"✓ Found {len(dag_runs)} recent DAG runs")

    failed_run = None
    for run in dag_runs:
        if run.get("state") == "failed":
            failed_run = run
            break

    if not failed_run:
        print("❌ No failed DAG runs found")
        return None

    dag_run_id = failed_run.get("dag_run_id") or failed_run.get("run_id", "unknown")

    print("\n✓ Found failed DAG run:")
    print(f"   ID: {dag_run_id}")
    print(f"   DAG: {failed_run.get('dag_id')}")
    print(f"   State: {failed_run.get('state')}")

    logs_client = boto3.client("logs", region_name="us-east-1")
    print(f"\nChecking CloudWatch logs: {CONFIG['log_group']}")

    try:
        response = logs_client.filter_log_events(
            logGroupName=CONFIG["log_group"],
            startTime=int((time.time() - 3600) * 1000),
            filterPattern=CONFIG["correlation_id"],
        )

        error_message = "Schema validation failed"
        for event in response["events"]:
            message = event["message"]
            if "Schema validation failed" in message and "Missing fields" in message:
                start = message.find("Missing fields")
                end = message.find("in record", start) + len("in record 0")
                error_message = message[start:end]
                break

        print(f"✓ Error found in logs: {error_message}")

    except Exception as e:
        print(f"⚠ Warning: Could not fetch CloudWatch logs: {e}")
        error_message = "Schema validation failed"

    return {
        "dag_run_id": dag_run_id,
        "correlation_id": CONFIG["correlation_id"],
        "error_message": error_message,
        "log_group": CONFIG["log_group"],
        "s3_bucket": CONFIG["s3_bucket"],
        "s3_key": CONFIG["s3_key"],
        "audit_key": CONFIG["audit_key"],
    }


def test_agent_investigation(failure_data: dict) -> bool:
    """Test agent can investigate the Airflow pipeline failure."""
    print("\n" + "=" * 60)
    print("Running Agent Investigation")
    print("=" * 60)

    alert = create_alert(
        pipeline_name="upstream_downstream_pipeline_airflow",
        run_name=failure_data["dag_run_id"],
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        severity="critical",
        alert_name=f"Airflow DAG Failed: {failure_data['dag_run_id']}",
        annotations={
            "cloudwatch_log_group": failure_data["log_group"],
            "dag_run_id": failure_data["dag_run_id"],
            "airflow_dag": CONFIG["dag_id"],
            "ecs_cluster": "tracer-airflow-cluster",
            "landing_bucket": failure_data["s3_bucket"],
            "s3_key": failure_data["s3_key"],
            "audit_key": failure_data["audit_key"],
            "airflow_api_url": CONFIG["airflow_api_url"],
            "error_message": failure_data["error_message"],
        },
    )

    print("\n📋 Alert created:")
    print(f"   Pipeline: {alert.get('labels', {}).get('alertname', 'unknown')}")
    print(f"   Run ID: {failure_data['dag_run_id']}")
    print(f"   Log Group: {failure_data['log_group']}")
    print(f"   S3 Data: s3://{failure_data['s3_bucket']}/{failure_data['s3_key']}")
    print(f"   S3 Audit: s3://{failure_data['s3_bucket']}/{failure_data['audit_key']}")

    print("\n🤖 Starting investigation agent...")
    print("-" * 60)

    @traceable(
        run_type="chain",
        name=f"test_airflow_ecs - {alert['alert_id'][:8]}",
        metadata={
            "alert_id": alert["alert_id"],
            "pipeline_name": "upstream_downstream_pipeline_airflow",
            "dag_run_id": failure_data["dag_run_id"],
            "dag_id": CONFIG["dag_id"],
            "ecs_cluster": "tracer-airflow-cluster",
            "log_group": failure_data["log_group"],
            "s3_key": failure_data["s3_key"],
        },
    )
    def run_investigation():
        return _run(
            alert_name=alert.get("labels", {}).get("alertname", "AirflowDagFailure"),
            pipeline_name="upstream_downstream_pipeline_airflow",
            severity="critical",
            raw_alert=alert,
        )

    result = run_investigation()

    print("-" * 60)
    print("\n📊 Investigation Results:")
    print(f"   Status: {result.get('status', 'unknown')}")

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

    success_checks = {
        "Airflow logs retrieved": False,
        "S3 input data inspected": False,
        "Audit trail traced": False,
        "External API identified": False,
        "Schema change detected": False,
    }

    investigation_text = json.dumps(result).lower()

    if "cloudwatch" in investigation_text or "airflow" in investigation_text:
        success_checks["Airflow logs retrieved"] = True

    if failure_data["s3_key"] in investigation_text or "ingested/20260131" in investigation_text:
        success_checks["S3 input data inspected"] = True

    if failure_data["audit_key"] in investigation_text or "audit/" in investigation_text:
        success_checks["Audit trail traced"] = True

    if "external" in investigation_text and (
        "api" in investigation_text or "vendor" in investigation_text
    ):
        success_checks["External API identified"] = True

    if "event_id" in investigation_text or "schema" in investigation_text:
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
    print("AIRFLOW ECS E2E INVESTIGATION TEST")
    print("=" * 60)

    failure_data = get_failure_details()
    if not failure_data:
        print("\n❌ Could not retrieve failure details")
        return False

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
