"""Lambda error logs (filtered invocation logs)."""

from __future__ import annotations

from app.tools.LambdaInvocationLogsTool import (
    _lambda_available,
    _lambda_name,
    get_lambda_invocation_logs,
)
from app.tools.tool_decorator import tool


def _extract_lambda_errors_params(sources: dict[str, dict]) -> dict:
    return {"function_name": _lambda_name(sources), "limit": 50}


@tool(
    name="get_lambda_errors",
    display_name="Lambda errors",
    source="cloudwatch",
    description="Get Lambda function error logs.",
    use_cases=[
        "Quickly finding error messages from a Lambda function",
        "Understanding Lambda failure patterns",
        "Identifying root cause of Lambda failures",
    ],
    requires=["function_name"],
    input_schema={
        "type": "object",
        "properties": {
            "function_name": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["function_name"],
    },
    is_available=_lambda_available,
    extract_params=_extract_lambda_errors_params,
)
def get_lambda_errors(function_name: str, limit: int = 50) -> dict:
    """Get Lambda function error logs (filtered view of invocation logs)."""
    return get_lambda_invocation_logs(function_name=function_name, filter_errors=True, limit=limit)
