"""
MWAA Test Case - Use Case Logic.

Simulates triggering an MWAA DAG and optionally injecting schema changes
to create upstream/downstream failure scenarios.

This module handles:
1. Invoking the Lambda function to ingest data
2. Triggering the MWAA DAG
3. Waiting for DAG completion
4. Returning success/failure status
"""

import json
import os
import time
from typing import Any

try:
    import boto3
except ImportError:
    boto3 = None

_pipeline_context = {
    "pipeline_name": "ingest_transform",
    "environment_name": os.getenv("MWAA_ENVIRONMENT", "tracer-test-env"),
    "lambda_function": os.getenv("API_INGESTER_FUNCTION", "tracer-test-api-ingester"),
    "initialized": False,
}


def invoke_lambda_ingester(
    inject_schema_change: bool = False,
    trigger_dag: bool = True,
) -> dict[str, Any]:
    """
    Invoke the API ingester Lambda function.

    Args:
        inject_schema_change: If True, Lambda will omit customer_id field
        trigger_dag: If True, Lambda will trigger the MWAA DAG

    Returns:
        Lambda invocation result
    """
    if boto3 is None:
        raise RuntimeError("boto3 not available")

    lambda_client = boto3.client("lambda")

    payload = {
        "inject_schema_change": inject_schema_change,
        "trigger_dag": trigger_dag,
    }

    response = lambda_client.invoke(
        FunctionName=_pipeline_context["lambda_function"],
        InvocationType="RequestResponse",
        Payload=json.dumps(payload),
    )

    result = json.loads(response["Payload"].read().decode())
    return result


def wait_for_dag_completion(
    dag_id: str,
    execution_date: str | None = None,
    timeout_seconds: int = 300,
    poll_interval: int = 10,
) -> dict[str, Any]:
    """
    Wait for a DAG run to complete.

    Args:
        dag_id: Airflow DAG ID
        execution_date: Optional specific execution date to wait for
        timeout_seconds: Maximum time to wait
        poll_interval: Seconds between status checks

    Returns:
        Final DAG run status
    """
    from app.agent.tools.clients.mwaa_client import get_dag_runs

    env_name = _pipeline_context["environment_name"]
    start_time = time.time()

    while (time.time() - start_time) < timeout_seconds:
        result = get_dag_runs(env_name, dag_id, limit=5)

        if not result.get("success"):
            print(f"Error getting DAG runs: {result.get('error')}")
            time.sleep(poll_interval)
            continue

        runs = result.get("data", {}).get("runs", [])
        if not runs:
            print("No DAG runs found yet...")
            time.sleep(poll_interval)
            continue

        latest_run = runs[0] if isinstance(runs, list) else runs
        state = latest_run.get("state", "unknown")

        print(f"DAG run state: {state}")

        if state in ["success", "failed"]:
            return {
                "status": state,
                "run": latest_run,
                "dag_id": dag_id,
            }

        time.sleep(poll_interval)

    return {
        "status": "timeout",
        "error": f"DAG run did not complete within {timeout_seconds} seconds",
        "dag_id": dag_id,
    }


def get_task_failure_details(
    dag_id: str,
    task_id: str,
    execution_date: str,
) -> dict[str, Any]:
    """
    Get details of a failed task.

    Args:
        dag_id: Airflow DAG ID
        task_id: Failed task ID
        execution_date: Execution date

    Returns:
        Task failure details including logs
    """
    from app.agent.tools.tool_actions.mwaa_actions import get_airflow_task_logs

    env_name = _pipeline_context["environment_name"]

    return get_airflow_task_logs(
        environment_name=env_name,
        dag_id=dag_id,
        task_id=task_id,
        execution_date=execution_date,
    )


def main(inject_schema_change: bool = False) -> dict[str, Any]:
    """
    Run the MWAA test case.

    Args:
        inject_schema_change: If True, inject a schema change to cause failure

    Returns:
        Test result with status and details
    """
    _pipeline_context["initialized"] = True
    dag_id = _pipeline_context["pipeline_name"]

    print(f"Invoking Lambda ingester (schema_change={inject_schema_change})...")
    lambda_result = invoke_lambda_ingester(
        inject_schema_change=inject_schema_change,
        trigger_dag=True,
    )

    if lambda_result.get("statusCode") != 200:
        return {
            "status": "failed",
            "stage": "lambda_invocation",
            "error": lambda_result.get("error", "Lambda invocation failed"),
            "pipeline_name": dag_id,
        }

    s3_key = lambda_result.get("s3_key")
    print(f"Data written to S3: {s3_key}")

    print("Waiting for DAG completion...")
    dag_result = wait_for_dag_completion(dag_id, timeout_seconds=300)

    if dag_result["status"] == "failed":
        return {
            "status": "failed",
            "stage": "dag_execution",
            "pipeline_name": dag_id,
            "s3_key": s3_key,
            "dag_run": dag_result.get("run"),
            "schema_change_injected": inject_schema_change,
        }

    if dag_result["status"] == "success":
        return {
            "status": "success",
            "pipeline_name": dag_id,
            "s3_key": s3_key,
            "dag_run": dag_result.get("run"),
        }

    return {
        "status": "unknown",
        "pipeline_name": dag_id,
        "result": dag_result,
    }


if __name__ == "__main__":
    import sys
    result = main(inject_schema_change="--inject-failure" in sys.argv)
    print(json.dumps(result, indent=2, default=str))
    sys.exit(0 if result["status"] == "success" else 1)
