"""
MWAA tool actions - LangChain tool implementation.

No printing, no LLM calls. Just fetch data and return typed results.
"""

from app.agent.tools.clients.mwaa_client import (
    get_dag_runs,
    get_mwaa_dag_processing_logs,
    get_mwaa_environment,
    get_mwaa_scheduler_logs,
    get_mwaa_task_logs,
    trigger_dag_run,
)


def get_airflow_task_logs(
    environment_name: str,
    dag_id: str,
    task_id: str,
    execution_date: str,
    try_number: int = 1,
    limit: int = 500,
) -> dict:
    """
    Fetch Airflow task logs from MWAA CloudWatch.

    Use this when investigating a failed Airflow task to get the execution logs
    and stack traces. Essential for understanding why a DAG task failed.

    Useful for:
    - Retrieving error tracebacks from failed Airflow tasks
    - Understanding task execution flow
    - Identifying data processing errors
    - Tracing upstream data issues

    Args:
        environment_name: MWAA environment name
        dag_id: Airflow DAG ID
        task_id: Airflow task ID
        execution_date: Execution date (ISO format)
        try_number: Task try number (default 1)
        limit: Maximum log events to return

    Returns:
        Dictionary with task logs and metadata
    """
    if not environment_name or not dag_id or not task_id:
        return {"error": "environment_name, dag_id, and task_id are required"}

    result = get_mwaa_task_logs(
        environment_name=environment_name,
        dag_id=dag_id,
        task_id=task_id,
        execution_date=execution_date,
        try_number=try_number,
        limit=limit,
    )

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "environment_name": environment_name,
            "dag_id": dag_id,
            "task_id": task_id,
        }

    data = result.get("data", {})
    events = data.get("events", [])
    log_messages = [event.get("message", "") for event in events]

    return {
        "found": bool(events),
        "environment_name": environment_name,
        "dag_id": dag_id,
        "task_id": task_id,
        "execution_date": execution_date,
        "log_group": data.get("log_group"),
        "log_streams": data.get("log_streams", []),
        "event_count": len(events),
        "task_logs": log_messages,
        "latest_log": log_messages[-1] if log_messages else None,
    }


def get_airflow_scheduler_errors(
    environment_name: str,
    limit: int = 100,
) -> dict:
    """
    Fetch Airflow scheduler error logs from MWAA.

    Use this to investigate scheduler-level issues that may affect DAG execution.

    Useful for:
    - Identifying DAG parsing errors
    - Understanding scheduler failures
    - Diagnosing task scheduling issues

    Args:
        environment_name: MWAA environment name
        limit: Maximum log events to return

    Returns:
        Dictionary with scheduler error logs
    """
    if not environment_name:
        return {"error": "environment_name is required"}

    result = get_mwaa_scheduler_logs(
        environment_name=environment_name,
        filter_pattern="ERROR",
        limit=limit,
    )

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "environment_name": environment_name,
        }

    data = result.get("data", {})
    events = data.get("events", [])
    log_messages = [event.get("message", "") for event in events]

    return {
        "found": bool(events),
        "environment_name": environment_name,
        "log_group": data.get("log_group"),
        "event_count": len(events),
        "error_logs": log_messages,
    }


def get_airflow_dag_processing_errors(
    environment_name: str,
    limit: int = 100,
) -> dict:
    """
    Fetch DAG processing error logs from MWAA.

    Use this to investigate DAG file parsing and import errors.

    Useful for:
    - Identifying syntax errors in DAG files
    - Understanding import failures
    - Diagnosing DAG registration issues

    Args:
        environment_name: MWAA environment name
        limit: Maximum log events to return

    Returns:
        Dictionary with DAG processing error logs
    """
    if not environment_name:
        return {"error": "environment_name is required"}

    result = get_mwaa_dag_processing_logs(
        environment_name=environment_name,
        limit=limit,
    )

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "environment_name": environment_name,
        }

    data = result.get("data", {})
    events = data.get("events", [])
    log_messages = [event.get("message", "") for event in events]

    return {
        "found": bool(events),
        "environment_name": environment_name,
        "log_group": data.get("log_group"),
        "event_count": len(events),
        "error_logs": log_messages,
    }


def get_mwaa_environment_info(environment_name: str) -> dict:
    """
    Get MWAA environment configuration and status.

    Use this to understand the MWAA environment setup and verify it's operational.

    Useful for:
    - Verifying MWAA environment status
    - Getting environment configuration details
    - Understanding DAG storage location

    Args:
        environment_name: MWAA environment name

    Returns:
        Dictionary with environment details
    """
    if not environment_name:
        return {"error": "environment_name is required"}

    result = get_mwaa_environment(environment_name)

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "environment_name": environment_name,
        }

    return {
        "found": True,
        **result.get("data", {}),
    }


def list_dag_runs(
    environment_name: str,
    dag_id: str,
    limit: int = 10,
) -> dict:
    """
    List recent DAG runs for investigation.

    Use this to find recent executions of a DAG and their statuses.

    Useful for:
    - Finding failed DAG runs
    - Understanding execution history
    - Identifying patterns in failures

    Args:
        environment_name: MWAA environment name
        dag_id: Airflow DAG ID
        limit: Maximum number of runs to return

    Returns:
        Dictionary with DAG run information
    """
    if not environment_name or not dag_id:
        return {"error": "environment_name and dag_id are required"}

    result = get_dag_runs(
        environment_name=environment_name,
        dag_id=dag_id,
        limit=limit,
    )

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "environment_name": environment_name,
            "dag_id": dag_id,
        }

    return {
        "found": True,
        "environment_name": environment_name,
        **result.get("data", {}),
    }


def trigger_airflow_dag(
    environment_name: str,
    dag_id: str,
    conf: dict | None = None,
) -> dict:
    """
    Trigger an Airflow DAG run.

    Use this to manually trigger a DAG for testing or re-execution.

    Args:
        environment_name: MWAA environment name
        dag_id: Airflow DAG ID
        conf: Optional configuration dict for the DAG run

    Returns:
        Dictionary with trigger result
    """
    if not environment_name or not dag_id:
        return {"error": "environment_name and dag_id are required"}

    result = trigger_dag_run(
        environment_name=environment_name,
        dag_id=dag_id,
        conf=conf,
    )

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "environment_name": environment_name,
            "dag_id": dag_id,
        }

    return {
        "triggered": True,
        "environment_name": environment_name,
        **result.get("data", {}),
    }
