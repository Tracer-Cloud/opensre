"""Lambda error logs (filtered invocation logs)."""

from __future__ import annotations

from app.tools.base import BaseTool
from app.tools.LambdaInvocationLogsTool import (
    LambdaInvocationLogsTool,
    _lambda_available,
    _lambda_name,
)


class LambdaErrorsTool(BaseTool):
    """Get Lambda function error logs (filtered view of invocation logs)."""

    name = "get_lambda_errors"
    source = "cloudwatch"
    description = "Get Lambda function error logs."
    use_cases = [
        "Quickly finding error messages from a Lambda function",
        "Understanding Lambda failure patterns",
        "Identifying root cause of Lambda failures",
    ]
    requires = ["function_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "function_name": {"type": "string"},
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["function_name"],
    }

    def is_available(self, sources: dict) -> bool:
        return _lambda_available(sources)

    def extract_params(self, sources: dict) -> dict:
        return {"function_name": _lambda_name(sources), "limit": 50}

    def run(self, function_name: str, limit: int = 50, **_kwargs) -> dict:
        return LambdaInvocationLogsTool().run(function_name=function_name, filter_errors=True, limit=limit)


get_lambda_errors = LambdaErrorsTool()
