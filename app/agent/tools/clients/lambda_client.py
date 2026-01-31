"""Lambda client for function inspection and log retrieval."""

import base64
import os
from io import BytesIO
from typing import Any
from zipfile import ZipFile

try:
    import boto3
    from botocore.exceptions import ClientError
except ImportError:
    boto3 = None
    ClientError = Exception


def _get_lambda_client():
    """Get or create Lambda client."""
    if boto3 is None:
        return None
    return boto3.client("lambda", region_name=os.getenv("AWS_REGION", "us-east-1"))


def _get_cloudwatch_logs_client():
    """Get or create CloudWatch Logs client."""
    if boto3 is None:
        return None
    return boto3.client("logs", region_name=os.getenv("AWS_REGION", "us-east-1"))


def get_function_configuration(function_name: str) -> dict[str, Any]:
    """
    Get Lambda function configuration.

    Args:
        function_name: Lambda function name or ARN

    Returns:
        dict with function configuration
    """
    client = _get_lambda_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        response = client.get_function_configuration(FunctionName=function_name)

        return {
            "success": True,
            "data": {
                "function_name": response.get("FunctionName"),
                "function_arn": response.get("FunctionArn"),
                "runtime": response.get("Runtime"),
                "handler": response.get("Handler"),
                "code_size": response.get("CodeSize"),
                "timeout": response.get("Timeout"),
                "memory_size": response.get("MemorySize"),
                "last_modified": response.get("LastModified"),
                "role": response.get("Role"),
                "environment": response.get("Environment", {}).get("Variables", {}),
                "description": response.get("Description"),
                "version": response.get("Version"),
                "state": response.get("State"),
                "state_reason": response.get("StateReason"),
                "layers": [
                    {"arn": layer.get("Arn"), "code_size": layer.get("CodeSize")}
                    for layer in response.get("Layers", [])
                ],
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def get_function_code(
    function_name: str,
    extract_files: bool = True,
    max_file_size: int = 10000,
) -> dict[str, Any]:
    """
    Get Lambda function deployment package.

    Args:
        function_name: Lambda function name or ARN
        extract_files: If True, extract and return file contents
        max_file_size: Maximum size in bytes for extracted files

    Returns:
        dict with function code location and optionally file contents
    """
    client = _get_lambda_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        response = client.get_function(FunctionName=function_name)

        code_location = response.get("Code", {}).get("Location")
        code_size = response.get("Configuration", {}).get("CodeSize", 0)
        repository_type = response.get("Code", {}).get("RepositoryType")

        result = {
            "success": True,
            "data": {
                "function_name": function_name,
                "code_location": code_location,
                "code_size": code_size,
                "repository_type": repository_type,
            },
        }

        if extract_files and code_location and code_size < 5 * 1024 * 1024:
            # Download and extract if code is under 5MB
            import requests

            zip_response = requests.get(code_location, timeout=30)
            if zip_response.status_code == 200:
                files = {}
                try:
                    with ZipFile(BytesIO(zip_response.content)) as zf:
                        for name in zf.namelist():
                            if name.endswith("/"):
                                continue
                            info = zf.getinfo(name)
                            if info.file_size <= max_file_size:
                                try:
                                    content = zf.read(name).decode("utf-8")
                                    files[name] = {
                                        "size": info.file_size,
                                        "content": content,
                                    }
                                except UnicodeDecodeError:
                                    files[name] = {
                                        "size": info.file_size,
                                        "binary": True,
                                    }
                            else:
                                files[name] = {
                                    "size": info.file_size,
                                    "truncated": True,
                                }
                    result["data"]["files"] = files
                    result["data"]["file_count"] = len(files)
                except Exception as e:
                    result["data"]["extract_error"] = str(e)

        return result
    except ClientError as e:
        return {"success": False, "error": str(e)}


def get_recent_invocations(
    function_name: str,
    limit: int = 50,
    filter_pattern: str | None = None,
) -> dict[str, Any]:
    """
    Get recent Lambda invocation logs from CloudWatch.

    Lambda logs are stored in /aws/lambda/{function_name}.

    Args:
        function_name: Lambda function name
        limit: Maximum log events to return
        filter_pattern: Optional CloudWatch filter pattern

    Returns:
        dict with invocation logs
    """
    logs_client = _get_cloudwatch_logs_client()
    if not logs_client:
        return {"success": False, "error": "boto3 not available"}

    log_group_name = f"/aws/lambda/{function_name}"

    try:
        kwargs = {
            "logGroupName": log_group_name,
            "limit": limit,
        }
        if filter_pattern:
            kwargs["filterPattern"] = filter_pattern

        response = logs_client.filter_log_events(**kwargs)
        events = response.get("events", [])

        # Parse events to identify invocations
        invocations = []
        current_invocation = None

        for event in events:
            message = event.get("message", "")
            timestamp = event.get("timestamp")

            if "START RequestId:" in message:
                if current_invocation:
                    invocations.append(current_invocation)
                request_id = (
                    message.split("RequestId: ")[1].split()[0] if "RequestId:" in message else None
                )
                current_invocation = {
                    "request_id": request_id,
                    "start_time": timestamp,
                    "logs": [message],
                }
            elif "END RequestId:" in message:
                if current_invocation:
                    current_invocation["end_time"] = timestamp
                    current_invocation["logs"].append(message)
            elif "REPORT RequestId:" in message:
                if current_invocation:
                    current_invocation["logs"].append(message)
                    # Parse REPORT for duration and memory
                    if "Duration:" in message:
                        try:
                            duration_part = message.split("Duration: ")[1].split()[0]
                            current_invocation["duration_ms"] = float(duration_part)
                        except (IndexError, ValueError):
                            pass
                    if "Memory Used:" in message:
                        try:
                            memory_part = message.split("Memory Used: ")[1].split()[0]
                            current_invocation["memory_used_mb"] = int(memory_part)
                        except (IndexError, ValueError):
                            pass
                    invocations.append(current_invocation)
                    current_invocation = None
            elif current_invocation:
                current_invocation["logs"].append(message)

        if current_invocation:
            invocations.append(current_invocation)

        return {
            "success": True,
            "data": {
                "log_group": log_group_name,
                "invocation_count": len(invocations),
                "invocations": invocations[-10:],  # Return last 10 invocations
                "raw_event_count": len(events),
            },
        }
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ResourceNotFoundException":
            return {
                "success": False,
                "error": f"Log group not found: {log_group_name}",
                "log_group": log_group_name,
            }
        return {"success": False, "error": str(e)}


def get_invocation_logs_by_request_id(
    function_name: str,
    request_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    """
    Get logs for a specific Lambda invocation by request ID.

    Args:
        function_name: Lambda function name
        request_id: Lambda request ID
        limit: Maximum log events to return

    Returns:
        dict with invocation logs
    """
    logs_client = _get_cloudwatch_logs_client()
    if not logs_client:
        return {"success": False, "error": "boto3 not available"}

    log_group_name = f"/aws/lambda/{function_name}"

    try:
        response = logs_client.filter_log_events(
            logGroupName=log_group_name,
            filterPattern=f'"{request_id}"',
            limit=limit,
        )

        events = response.get("events", [])
        log_messages = [event.get("message", "") for event in events]

        return {
            "success": True,
            "data": {
                "log_group": log_group_name,
                "request_id": request_id,
                "event_count": len(events),
                "logs": log_messages,
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def invoke_function(
    function_name: str,
    payload: dict[str, Any] | None = None,
    invocation_type: str = "RequestResponse",
) -> dict[str, Any]:
    """
    Invoke a Lambda function.

    Args:
        function_name: Lambda function name or ARN
        payload: Optional payload dict
        invocation_type: InvocationType (RequestResponse, Event, DryRun)

    Returns:
        dict with invocation result
    """
    client = _get_lambda_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    import json

    try:
        kwargs = {
            "FunctionName": function_name,
            "InvocationType": invocation_type,
        }
        if payload:
            kwargs["Payload"] = json.dumps(payload)

        response = client.invoke(**kwargs)

        result_payload = None
        if "Payload" in response:
            result_payload = json.loads(response["Payload"].read().decode())

        return {
            "success": True,
            "data": {
                "status_code": response.get("StatusCode"),
                "function_error": response.get("FunctionError"),
                "executed_version": response.get("ExecutedVersion"),
                "log_result": base64.b64decode(response.get("LogResult", "")).decode()
                if response.get("LogResult")
                else None,
                "payload": result_payload,
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}


def list_functions(
    max_items: int = 50,
    function_version: str = "ALL",
) -> dict[str, Any]:
    """
    List Lambda functions in the account.

    Args:
        max_items: Maximum functions to return
        function_version: ALL or specific version

    Returns:
        dict with function list
    """
    client = _get_lambda_client()
    if not client:
        return {"success": False, "error": "boto3 not available"}

    try:
        response = client.list_functions(
            FunctionVersion=function_version,
            MaxItems=max_items,
        )

        functions = []
        for func in response.get("Functions", []):
            functions.append(
                {
                    "function_name": func.get("FunctionName"),
                    "function_arn": func.get("FunctionArn"),
                    "runtime": func.get("Runtime"),
                    "handler": func.get("Handler"),
                    "code_size": func.get("CodeSize"),
                    "timeout": func.get("Timeout"),
                    "memory_size": func.get("MemorySize"),
                    "last_modified": func.get("LastModified"),
                }
            )

        return {
            "success": True,
            "data": {
                "functions": functions,
                "count": len(functions),
            },
        }
    except ClientError as e:
        return {"success": False, "error": str(e)}
