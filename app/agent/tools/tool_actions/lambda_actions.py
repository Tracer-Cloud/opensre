"""
Lambda tool actions - LangChain tool implementation.

No printing, no LLM calls. Just fetch data and return typed results.
"""

from app.agent.tools.clients.lambda_client import (
    get_function_code,
    get_function_configuration,
    get_invocation_logs_by_request_id,
    get_recent_invocations,
)


def inspect_lambda_function(function_name: str, include_code: bool = True) -> dict:
    """
    Inspect a Lambda function's configuration and optionally its code.

    Use this when investigating a Lambda function that may be involved
    in a data pipeline failure.

    Useful for:
    - Understanding function configuration (timeout, memory, env vars)
    - Reviewing function code for data transformation logic
    - Identifying environment-related issues
    - Finding integration points with other services

    Args:
        function_name: Lambda function name or ARN
        include_code: If True, also retrieve and extract function code

    Returns:
        Dictionary with function configuration and optionally code
    """
    if not function_name:
        return {"error": "function_name is required"}

    # Get configuration
    config_result = get_function_configuration(function_name)

    if not config_result.get("success"):
        return {
            "error": config_result.get("error", "Unknown error"),
            "function_name": function_name,
        }

    config = config_result.get("data", {})

    result = {
        "found": True,
        "function_name": config.get("function_name"),
        "function_arn": config.get("function_arn"),
        "runtime": config.get("runtime"),
        "handler": config.get("handler"),
        "timeout": config.get("timeout"),
        "memory_size": config.get("memory_size"),
        "code_size": config.get("code_size"),
        "last_modified": config.get("last_modified"),
        "state": config.get("state"),
        "environment_variables": config.get("environment", {}),
        "description": config.get("description"),
        "layers": config.get("layers", []),
    }

    if include_code:
        code_result = get_function_code(function_name, extract_files=True)
        if code_result.get("success"):
            code_data = code_result.get("data", {})
            result["code"] = {
                "file_count": code_data.get("file_count", 0),
                "files": code_data.get("files", {}),
            }

    return result


def get_lambda_invocation_logs(
    function_name: str,
    request_id: str | None = None,
    filter_errors: bool = False,
    limit: int = 50,
) -> dict:
    """
    Get Lambda invocation logs from CloudWatch.

    Use this to retrieve execution logs from Lambda functions to understand
    what happened during function invocations.

    Useful for:
    - Finding error messages and stack traces
    - Understanding data processing flow
    - Identifying issues with external API calls
    - Tracing data transformation logic

    Args:
        function_name: Lambda function name
        request_id: Optional specific request ID to look up
        filter_errors: If True, filter for ERROR messages
        limit: Maximum log events to return

    Returns:
        Dictionary with invocation logs
    """
    if not function_name:
        return {"error": "function_name is required"}

    if request_id:
        result = get_invocation_logs_by_request_id(function_name, request_id, limit)
        if not result.get("success"):
            return {
                "error": result.get("error", "Unknown error"),
                "function_name": function_name,
                "request_id": request_id,
            }

        data = result.get("data", {})
        return {
            "found": bool(data.get("logs")),
            "function_name": function_name,
            "request_id": request_id,
            "log_group": data.get("log_group"),
            "event_count": data.get("event_count", 0),
            "logs": data.get("logs", []),
        }

    # Get recent invocations
    filter_pattern = "ERROR" if filter_errors else None
    result = get_recent_invocations(function_name, limit, filter_pattern)

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "function_name": function_name,
        }

    data = result.get("data", {})
    invocations = data.get("invocations", [])

    # Flatten logs from invocations
    all_logs = []
    for inv in invocations:
        for log in inv.get("logs", []):
            all_logs.append(
                {
                    "request_id": inv.get("request_id"),
                    "message": log,
                }
            )

    return {
        "found": bool(invocations),
        "function_name": function_name,
        "log_group": data.get("log_group"),
        "invocation_count": data.get("invocation_count", 0),
        "invocations": [
            {
                "request_id": inv.get("request_id"),
                "duration_ms": inv.get("duration_ms"),
                "memory_used_mb": inv.get("memory_used_mb"),
                "log_count": len(inv.get("logs", [])),
            }
            for inv in invocations
        ],
        "recent_logs": all_logs[-20:],  # Last 20 log messages
    }


def get_lambda_errors(function_name: str, limit: int = 50) -> dict:
    """
    Get Lambda function error logs.

    Shortcut for filtering Lambda logs to only show errors.

    Useful for:
    - Quickly finding error messages
    - Understanding failure patterns
    - Identifying root cause of Lambda failures

    Args:
        function_name: Lambda function name
        limit: Maximum log events to return

    Returns:
        Dictionary with error logs
    """
    return get_lambda_invocation_logs(
        function_name=function_name,
        filter_errors=True,
        limit=limit,
    )


def get_lambda_configuration(function_name: str) -> dict:
    """
    Get Lambda function configuration details.

    Lightweight version of inspect_lambda_function without code retrieval.

    Useful for:
    - Quick configuration checks
    - Environment variable inspection
    - Timeout and memory settings

    Args:
        function_name: Lambda function name or ARN

    Returns:
        Dictionary with function configuration
    """
    if not function_name:
        return {"error": "function_name is required"}

    result = get_function_configuration(function_name)

    if not result.get("success"):
        return {
            "error": result.get("error", "Unknown error"),
            "function_name": function_name,
        }

    config = result.get("data", {})

    return {
        "found": True,
        "function_name": config.get("function_name"),
        "runtime": config.get("runtime"),
        "handler": config.get("handler"),
        "timeout": config.get("timeout"),
        "memory_size": config.get("memory_size"),
        "last_modified": config.get("last_modified"),
        "state": config.get("state"),
        "environment_variables": config.get("environment", {}),
    }
