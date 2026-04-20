"""Inspect Lambda configuration and code."""

from __future__ import annotations

from app.services.lambda_client import get_function_code, get_function_configuration
from app.tools.LambdaInvocationLogsTool import _lambda_available, _lambda_name
from app.tools.tool_decorator import tool


def _extract_inspect_lambda_params(sources: dict[str, dict]) -> dict:
    return {"function_name": _lambda_name(sources), "include_code": True}


@tool(
    name="inspect_lambda_function",
    source="cloudwatch",
    description="Inspect a Lambda function's configuration and optionally its code.",
    use_cases=[
        "Understanding function configuration (timeout, memory, env vars)",
        "Reviewing function code for data transformation logic",
        "Identifying environment-related issues",
        "Finding integration points with other services",
    ],
    requires=["function_name"],
    input_schema={
        "type": "object",
        "properties": {
            "function_name": {"type": "string"},
            "include_code": {"type": "boolean", "default": True},
        },
        "required": ["function_name"],
    },
    is_available=_lambda_available,
    extract_params=_extract_inspect_lambda_params,
)
def inspect_lambda_function(function_name: str, include_code: bool = True) -> dict:
    """Inspect a Lambda function's configuration and optionally its code."""
    if not function_name:
        return {"error": "function_name is required"}

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
