"""Lambda configuration (lightweight, no code)."""

from __future__ import annotations

from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.aws.lambda_client import get_function_configuration
from integrations.aws_lambda.availability import lambda_available, lambda_name


def _extract_lambda_config_params(sources: dict[str, dict]) -> dict:
    return {"function_name": lambda_name(sources)}


@tool(
    name="get_lambda_configuration",
    source="cloudwatch",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    description="Get Lambda function configuration details (lightweight — no code retrieval).",
    use_cases=[
        "Quick configuration checks for Lambda functions",
        "Environment variable inspection",
        "Timeout and memory settings review",
    ],
    requires=["function_name"],
    input_schema={
        "type": "object",
        "properties": {
            "function_name": {"type": "string"},
        },
        "required": ["function_name"],
    },
    is_available=lambda_available,
    extract_params=_extract_lambda_config_params,
)
def get_lambda_configuration(function_name: str) -> dict:
    """Get Lambda function configuration details (lightweight, no code)."""
    if not function_name:
        return {"error": "function_name is required"}

    result = get_function_configuration(function_name)
    if not result.get("success"):
        return {"error": result.get("error", "Unknown error"), "function_name": function_name}

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
