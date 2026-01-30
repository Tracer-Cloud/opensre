"""MWAA (Managed Workflows for Apache Airflow) client for task log retrieval."""

import os
from typing import Any

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    ClientError = Exception


def _get_mwaa_client():
    """Get or create MWAA client."""
    if boto3 is None:
        return None
    return boto3.client("mwaa", region_name=os.getenv("AWS_REGION", "us-east-1"))


def _get_cloudwatch_logs_client():
    """Get or create CloudWatch Logs client for MWAA logs."""
    if boto3 is None:
        return None
    return boto3.client("logs", region_name=os.getenv("AWS_REGION", "us-east-1"))


def get_mwaa_environment(environment_name: str) -> dict[str, Any]:
    """
    Get MWAA environment details.

    Args:
        environment_name: Name of the MWAA environment

    Returns:
        dict with environment details or error info
    """
    client = _get_mwaa_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        response = client.get_environment(Name=environment_name)
        env = response.get("Environment", {})
        return {
            "success": True,
            "data": {
                "name": env.get("Name"),
                "status": env.get("Status"),
                "airflow_version": env.get("AirflowVersion"),
                "environment_class": env.get("EnvironmentClass"),
                "webserver_url": env.get("WebserverUrl"),
                "execution_role_arn": env.get("ExecutionRoleArn"),
                "source_bucket_arn": env.get("SourceBucketArn"),
                "dag_s3_path": env.get("DagS3Path"),
                "logging_configuration": env.get("LoggingConfiguration"),
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def get_mwaa_task_logs(
    environment_name: str,
    dag_id: str,
    task_id: str,
    execution_date: str,
    try_number: int = 1,
    limit: int = 500,
) -> dict[str, Any]:
    """
    Get MWAA task logs from CloudWatch.

    MWAA task logs are stored in CloudWatch with the pattern:
    /aws/mwaa/{environment_name}/DAG/{dag_id}/{task_id}/{execution_date}/{try_number}

    Args:
        environment_name: Name of the MWAA environment
        dag_id: Airflow DAG ID
        task_id: Airflow task ID
        execution_date: Execution date in ISO format (e.g., "2024-01-30T06:00:00+00:00")
        try_number: Task try number (default 1)
        limit: Maximum number of log events to return

    Returns:
        dict with log events or error info
    """
    logs_client = _get_cloudwatch_logs_client()
    if not logs_client:
        return {"success": False, "error": "boto3 not available"}

    log_group_name = f"airflow-{environment_name}-Task"
    execution_date_formatted = execution_date.replace(":", "_").replace("+", "_")
    log_stream_prefix = f"{dag_id}/{task_id}/{execution_date_formatted}/{try_number}"

    try:
        streams_response = logs_client.describe_log_streams(
            logGroupName=log_group_name,
            logStreamNamePrefix=log_stream_prefix,
            limit=10,
        )

        log_streams = streams_response.get("logStreams", [])
        if not log_streams:
            return {
                "success": False,
                "error": f"No log streams found for prefix: {log_stream_prefix}",
                "log_group": log_group_name,
                "log_stream_prefix": log_stream_prefix,
            }

        all_events = []
        for stream in log_streams:
            stream_name = stream.get("logStreamName")
            events_response = logs_client.get_log_events(
                logGroupName=log_group_name,
                logStreamName=stream_name,
                limit=limit,
            )
            events = events_response.get("events", [])
            for event in events:
                event["logStreamName"] = stream_name
            all_events.extend(events)

        all_events.sort(key=lambda x: x.get("timestamp", 0))

        return {
            "success": True,
            "data": {
                "log_group": log_group_name,
                "log_streams": [s.get("logStreamName") for s in log_streams],
                "events": all_events[-limit:],
                "event_count": len(all_events),
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def get_mwaa_dag_processing_logs(
    environment_name: str,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Get MWAA DAG processing logs (scheduler/parser logs).

    Args:
        environment_name: Name of the MWAA environment
        limit: Maximum number of log events to return

    Returns:
        dict with log events or error info
    """
    logs_client = _get_cloudwatch_logs_client()
    if not logs_client:
        return {"success": False, "error": "boto3 not available"}

    log_group_name = f"airflow-{environment_name}-DAGProcessing"

    try:
        response = logs_client.filter_log_events(
            logGroupName=log_group_name,
            filterPattern="ERROR",
            limit=limit,
        )

        return {
            "success": True,
            "data": {
                "log_group": log_group_name,
                "events": response.get("events", []),
                "event_count": len(response.get("events", [])),
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def get_mwaa_scheduler_logs(
    environment_name: str,
    filter_pattern: str | None = "ERROR",
    limit: int = 100,
) -> dict[str, Any]:
    """
    Get MWAA scheduler logs.

    Args:
        environment_name: Name of the MWAA environment
        filter_pattern: Log filter pattern (default "ERROR")
        limit: Maximum number of log events to return

    Returns:
        dict with log events or error info
    """
    logs_client = _get_cloudwatch_logs_client()
    if not logs_client:
        return {"success": False, "error": "boto3 not available"}

    log_group_name = f"airflow-{environment_name}-Scheduler"

    try:
        kwargs = {
            "logGroupName": log_group_name,
            "limit": limit,
        }
        if filter_pattern:
            kwargs["filterPattern"] = filter_pattern

        response = logs_client.filter_log_events(**kwargs)

        return {
            "success": True,
            "data": {
                "log_group": log_group_name,
                "events": response.get("events", []),
                "event_count": len(response.get("events", [])),
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def create_cli_token(environment_name: str) -> dict[str, Any]:
    """
    Create a CLI token for accessing MWAA REST API.

    Args:
        environment_name: Name of the MWAA environment

    Returns:
        dict with CLI token and webserver hostname or error info
    """
    client = _get_mwaa_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        response = client.create_cli_token(Name=environment_name)
        return {
            "success": True,
            "data": {
                "cli_token": response.get("CliToken"),
                "webserver_hostname": response.get("WebServerHostname"),
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def trigger_dag_run(
    environment_name: str,
    dag_id: str,
    conf: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Trigger a DAG run via MWAA CLI.

    Args:
        environment_name: Name of the MWAA environment
        dag_id: DAG ID to trigger
        conf: Optional configuration dict to pass to the DAG run

    Returns:
        dict with trigger result or error info
    """
    import json

    import requests

    token_result = create_cli_token(environment_name)
    if not token_result.get("success"):
        return token_result

    cli_token = token_result["data"]["cli_token"]
    hostname = token_result["data"]["webserver_hostname"]

    conf_json = json.dumps(conf) if conf else "{}"
    command = f"dags trigger {dag_id} --conf '{conf_json}'"

    try:
        response = requests.post(
            f"https://{hostname}/aws_mwaa/cli",
            headers={
                "Authorization": f"Bearer {cli_token}",
                "Content-Type": "text/plain",
            },
            data=command,
            timeout=30,
        )

        if response.status_code == 200:
            import base64

            stdout = base64.b64decode(response.json().get("stdout", "")).decode()
            stderr = base64.b64decode(response.json().get("stderr", "")).decode()
            return {
                "success": True,
                "data": {
                    "stdout": stdout,
                    "stderr": stderr,
                    "dag_id": dag_id,
                },
            }
        return {
            "success": False,
            "error": f"HTTP {response.status_code}: {response.text}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


def get_dag_runs(
    environment_name: str,
    dag_id: str,
    limit: int = 10,
) -> dict[str, Any]:
    """
    Get recent DAG runs via MWAA CLI.

    Args:
        environment_name: Name of the MWAA environment
        dag_id: DAG ID to query
        limit: Maximum number of runs to return

    Returns:
        dict with DAG runs or error info
    """
    import requests

    token_result = create_cli_token(environment_name)
    if not token_result.get("success"):
        return token_result

    cli_token = token_result["data"]["cli_token"]
    hostname = token_result["data"]["webserver_hostname"]

    command = f"dags list-runs -d {dag_id} -o json"

    try:
        response = requests.post(
            f"https://{hostname}/aws_mwaa/cli",
            headers={
                "Authorization": f"Bearer {cli_token}",
                "Content-Type": "text/plain",
            },
            data=command,
            timeout=30,
        )

        if response.status_code == 200:
            import base64
            import json

            stdout = base64.b64decode(response.json().get("stdout", "")).decode()
            try:
                runs = json.loads(stdout)
                return {
                    "success": True,
                    "data": {
                        "dag_id": dag_id,
                        "runs": runs[:limit] if isinstance(runs, list) else runs,
                    },
                }
            except json.JSONDecodeError:
                return {
                    "success": True,
                    "data": {
                        "dag_id": dag_id,
                        "raw_output": stdout,
                    },
                }
        return {
            "success": False,
            "error": f"HTTP {response.status_code}: {response.text}",
        }
    except Exception as e:
        return {"success": False, "error": str(e)}
