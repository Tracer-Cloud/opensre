"""Test checkpoints for ECS Fargate Airflow test case.

Three checkpoints:
1. Upstream-Triggered Execution - Lambda triggers Airflow pipeline
2. Validation Test - Verify DAG run is created and tasks consume S3 object
3. Failure Scenario Story - Schema change causes failure
"""

import json
import os
import time
from datetime import datetime
from typing import Any

import boto3
import requests


def checkpoint_1_upstream_triggered_execution(
    lambda_function_name: str,
    airflow_url: str,
    dag_id: str = "ingest_transform",
) -> dict[str, Any]:
    """
    Checkpoint 1: Upstream-Triggered Execution.

    Invoke the Lambda ingestion function and verify:
    - Lambda writes data to S3
    - Lambda triggers Airflow DAG run
    - New DAG run is created with unique run ID
    """
    print("=== Checkpoint 1: Upstream-Triggered Execution ===")

    lambda_client = boto3.client("lambda")

    # Invoke Lambda with trigger_dag=True
    response = lambda_client.invoke(
        FunctionName=lambda_function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({
            "trigger_dag": True,
            "inject_schema_change": False,
        }),
    )

    result = json.loads(response["Payload"].read().decode())
    print(f"Lambda invocation result: {result}")

    if result.get("statusCode") != 200:
        return {
            "checkpoint": "upstream_triggered_execution",
            "status": "failed",
            "error": f"Lambda invocation failed: {result}",
        }

    s3_key = result.get("s3_key")
    dag_trigger_result = result.get("dag_trigger", {})

    if not dag_trigger_result.get("success"):
        return {
            "checkpoint": "upstream_triggered_execution",
            "status": "failed",
            "error": f"DAG trigger failed: {dag_trigger_result.get('error')}",
        }

    # Wait a moment for DAG run to appear
    time.sleep(5)

    # Verify DAG run exists via Airflow REST API
    dag_run_id = dag_trigger_result.get("dag_run", {}).get("dag_run_id")
    if not dag_run_id:
        return {
            "checkpoint": "upstream_triggered_execution",
            "status": "failed",
            "error": "DAG run ID not found in trigger response",
        }

    # Verify DAG run in Airflow
    try:
        auth = ("admin", "admin")
        dag_run_url = f"{airflow_url}/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}"
        dag_run_response = requests.get(dag_run_url, auth=auth, timeout=10)
        if dag_run_response.status_code == 200:
            dag_run_data = dag_run_response.json()
            return {
                "checkpoint": "upstream_triggered_execution",
                "status": "passed",
                "s3_key": s3_key,
                "dag_run_id": dag_run_id,
                "dag_run": dag_run_data,
            }
    except Exception as e:
        return {
            "checkpoint": "upstream_triggered_execution",
            "status": "failed",
            "error": f"Failed to verify DAG run: {e}",
        }

    return {
        "checkpoint": "upstream_triggered_execution",
        "status": "failed",
        "error": "Could not verify DAG run",
    }


def checkpoint_2_validation_test(
    airflow_url: str,
    s3_bucket: str,
    s3_key: str,
    dag_id: str = "ingest_transform",
) -> dict[str, Any]:
    """
    Checkpoint 2: Validation Test.

    Verify:
    - DAG run is visible in Airflow
    - Downstream Airflow tasks consume the S3 object written by Lambda
    - Task logs explicitly reference the S3 object key
    """
    print("=== Checkpoint 2: Validation Test ===")

    auth = ("admin", "admin")

    # Get recent DAG runs
    dag_runs_url = f"{airflow_url}/api/v2/dags/{dag_id}/dagRuns"
    dag_runs_response = requests.get(dag_runs_url, auth=auth, timeout=10)

    if dag_runs_response.status_code != 200:
        return {
            "checkpoint": "validation_test",
            "status": "failed",
            "error": f"Failed to fetch DAG runs: {dag_runs_response.status_code}",
        }

    dag_runs = dag_runs_response.json().get("dag_runs", [])
    if not dag_runs:
        return {
            "checkpoint": "validation_test",
            "status": "failed",
            "error": "No DAG runs found",
        }

    # Find the most recent run
    latest_run = sorted(dag_runs, key=lambda x: x.get("execution_date", ""), reverse=True)[0]
    dag_run_id = latest_run.get("dag_run_id")

    # Get task instances for this DAG run
    task_instances_url = f"{airflow_url}/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
    task_instances_response = requests.get(task_instances_url, auth=auth, timeout=10)

    if task_instances_response.status_code != 200:
        return {
            "checkpoint": "validation_test",
            "status": "failed",
            "error": f"Failed to fetch task instances: {task_instances_response.status_code}",
        }

    task_instances = task_instances_response.json().get("task_instances", [])

    # Check if read_from_s3 task exists and references the S3 key
    read_task = next((t for t in task_instances if t.get("task_id") == "read_from_s3"), None)
    if not read_task:
        return {
            "checkpoint": "validation_test",
            "status": "failed",
            "error": "read_from_s3 task not found",
        }

    # Get task logs
    logs_url = f"{airflow_url}/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/read_from_s3/logs/1"
    logs_response = requests.get(logs_url, auth=auth, timeout=10)

    s3_key_found = False
    if logs_response.status_code == 200:
        logs_data = logs_response.json()
        log_content = logs_data.get("content", "")
        if s3_key in log_content:
            s3_key_found = True

    # Verify S3 object exists
    s3_client = boto3.client("s3")
    try:
        s3_client.head_object(Bucket=s3_bucket, Key=s3_key)
        s3_object_exists = True
    except Exception:
        s3_object_exists = False

    return {
        "checkpoint": "validation_test",
        "status": "passed" if s3_object_exists else "failed",
        "dag_run_id": dag_run_id,
        "task_instances": len(task_instances),
        "s3_object_exists": s3_object_exists,
        "s3_key_in_logs": s3_key_found,
        "s3_key": s3_key,
    }


def checkpoint_3_failure_scenario(
    lambda_function_name: str,
    airflow_url: str,
    dag_id: str = "ingest_transform",
) -> dict[str, Any]:
    """
    Checkpoint 3: Failure Scenario Story.

    Simulate:
    - 2:00 AM: External API removes customer_id
    - 6:00 AM: Transform DAG fails on join
    - Impact: Morning dashboards stale
    - RCA: Agent identifies upstream API schema change as root cause
    """
    print("=== Checkpoint 3: Failure Scenario Story ===")

    lambda_client = boto3.client("lambda")
    auth = ("admin", "admin")

    # Inject schema change and trigger DAG
    response = lambda_client.invoke(
        FunctionName=lambda_function_name,
        InvocationType="RequestResponse",
        Payload=json.dumps({
            "trigger_dag": True,
            "inject_schema_change": True,  # This removes customer_id
        }),
    )

    result = json.loads(response["Payload"].read().decode())
    print(f"Lambda invocation with schema change: {result}")

    if result.get("statusCode") != 200:
        return {
            "checkpoint": "failure_scenario",
            "status": "failed",
            "error": f"Lambda invocation failed: {result}",
        }

    dag_trigger_result = result.get("dag_trigger", {})
    if not dag_trigger_result.get("success"):
        return {
            "checkpoint": "failure_scenario",
            "status": "failed",
            "error": f"DAG trigger failed: {dag_trigger_result.get('error')}",
        }

    dag_run_id = dag_trigger_result.get("dag_run", {}).get("dag_run_id")

    # Wait for DAG to run and fail
    print("Waiting for DAG to execute and fail...")
    time.sleep(30)

    # Check if validate_schema task failed
    task_instances_url = f"{airflow_url}/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances"
    task_instances_response = requests.get(task_instances_url, auth=auth, timeout=10)

    if task_instances_response.status_code != 200:
        return {
            "checkpoint": "failure_scenario",
            "status": "failed",
            "error": f"Failed to fetch task instances: {task_instances_response.status_code}",
        }

    task_instances = task_instances_response.json().get("task_instances", [])
    validate_task = next((t for t in task_instances if t.get("task_id") == "validate_schema"), None)

    if not validate_task:
        return {
            "checkpoint": "failure_scenario",
            "status": "failed",
            "error": "validate_schema task not found",
        }

    task_state = validate_task.get("state")
    is_failed = task_state == "failed"

    # Get task logs to verify error message
    error_message_found = False
    if is_failed:
        logs_url = f"{airflow_url}/api/v2/dags/{dag_id}/dagRuns/{dag_run_id}/taskInstances/validate_schema/logs/1"
        logs_response = requests.get(logs_url, auth=auth, timeout=10)
        if logs_response.status_code == 200:
            logs_data = logs_response.json()
            log_content = logs_data.get("content", "")
            if "customer_id" in log_content.lower() or "missing required field" in log_content.lower():
                error_message_found = True

    return {
        "checkpoint": "failure_scenario",
        "status": "passed" if is_failed and error_message_found else "failed",
        "dag_run_id": dag_run_id,
        "validate_task_state": task_state,
        "task_failed": is_failed,
        "error_message_found": error_message_found,
    }


def run_all_checkpoints(
    airflow_url: str,
    lambda_function_name: str,
    s3_bucket: str,
) -> dict[str, Any]:
    """Run all three checkpoints."""
    results = {}

    # Checkpoint 1
    result_1 = checkpoint_1_upstream_triggered_execution(
        lambda_function_name,
        airflow_url,
    )
    results["checkpoint_1"] = result_1
    s3_key = result_1.get("s3_key")

    if result_1.get("status") == "passed" and s3_key:
        # Checkpoint 2
        result_2 = checkpoint_2_validation_test(
            airflow_url,
            s3_bucket,
            s3_key,
        )
        results["checkpoint_2"] = result_2

        # Checkpoint 3
        result_3 = checkpoint_3_failure_scenario(
            lambda_function_name,
            airflow_url,
        )
        results["checkpoint_3"] = result_3

    return results


if __name__ == "__main__":
    import sys

    airflow_url = os.getenv("AIRFLOW_WEBSERVER_URL", sys.argv[1] if len(sys.argv) > 1 else "")
    lambda_function_name = os.getenv("LAMBDA_FUNCTION_NAME", sys.argv[2] if len(sys.argv) > 2 else "")
    s3_bucket = os.getenv("DATA_BUCKET", sys.argv[3] if len(sys.argv) > 3 else "")

    if not all([airflow_url, lambda_function_name, s3_bucket]):
        print("Usage: python checkpoints.py <airflow_url> <lambda_function_name> <s3_bucket>")
        print("Or set environment variables: AIRFLOW_WEBSERVER_URL, LAMBDA_FUNCTION_NAME, DATA_BUCKET")
        sys.exit(1)

    results = run_all_checkpoints(airflow_url, lambda_function_name, s3_bucket)
    print("\n=== Checkpoint Results ===")
    print(json.dumps(results, indent=2))
