"""Lambda investigation tools."""

from __future__ import annotations

from app.tools.clients.lambda_client import (
    get_function_code,
    get_function_configuration,
    get_invocation_logs_by_request_id,
    get_recent_invocations,
)
from app.tools.tool_actions.base import BaseTool


def _lambda_available(sources: dict) -> bool:
    return bool(sources.get("lambda", {}).get("function_name"))


def _lambda_name(sources: dict) -> str:
    return str(sources.get("lambda", {}).get("function_name", ""))


class LambdaInvocationLogsTool(BaseTool):
    """Get Lambda invocation logs from CloudWatch."""

    name = "get_lambda_invocation_logs"
    source = "cloudwatch"
    description = "Get Lambda invocation logs from CloudWatch."
    use_cases = [
        "Finding error messages and stack traces from Lambda executions",
        "Understanding data processing flow in Lambda functions",
        "Identifying issues with external API calls made by Lambda",
        "Tracing data transformation logic through log output",
    ]
    requires = ["function_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "function_name": {"type": "string"},
            "request_id": {"type": "string"},
            "filter_errors": {"type": "boolean", "default": False},
            "limit": {"type": "integer", "default": 50},
        },
        "required": ["function_name"],
    }

    def is_available(self, sources: dict) -> bool:
        return _lambda_available(sources)

    def extract_params(self, sources: dict) -> dict:
        return {"function_name": _lambda_name(sources), "filter_errors": False, "limit": 50}

    def run(
        self,
        function_name: str,
        request_id: str | None = None,
        filter_errors: bool = False,
        limit: int = 50,
        **_kwargs,
    ) -> dict:
        if not function_name:
            return {"error": "function_name is required"}

        if request_id:
            result = get_invocation_logs_by_request_id(function_name, request_id, limit)
            if not result.get("success"):
                return {"error": result.get("error", "Unknown error"), "function_name": function_name, "request_id": request_id}
            data = result.get("data", {})
            return {
                "found": bool(data.get("logs")),
                "function_name": function_name,
                "request_id": request_id,
                "log_group": data.get("log_group"),
                "event_count": data.get("event_count", 0),
                "logs": data.get("logs", []),
            }

        filter_pattern = "ERROR" if filter_errors else None
        result = get_recent_invocations(function_name, limit, filter_pattern)
        if not result.get("success"):
            return {"error": result.get("error", "Unknown error"), "function_name": function_name}

        data = result.get("data", {})
        invocations = data.get("invocations", [])
        all_logs = [
            {"request_id": inv.get("request_id"), "message": log}
            for inv in invocations
            for log in inv.get("logs", [])
        ]

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
            "recent_logs": all_logs[-20:],
        }


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


class LambdaInspectTool(BaseTool):
    """Inspect a Lambda function's configuration and optionally its code."""

    name = "inspect_lambda_function"
    source = "cloudwatch"
    description = "Inspect a Lambda function's configuration and optionally its code."
    use_cases = [
        "Understanding function configuration (timeout, memory, env vars)",
        "Reviewing function code for data transformation logic",
        "Identifying environment-related issues",
        "Finding integration points with other services",
    ]
    requires = ["function_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "function_name": {"type": "string"},
            "include_code": {"type": "boolean", "default": True},
        },
        "required": ["function_name"],
    }

    def is_available(self, sources: dict) -> bool:
        return _lambda_available(sources)

    def extract_params(self, sources: dict) -> dict:
        return {"function_name": _lambda_name(sources), "include_code": True}

    def run(self, function_name: str, include_code: bool = True, **_kwargs) -> dict:
        if not function_name:
            return {"error": "function_name is required"}

        config_result = get_function_configuration(function_name)
        if not config_result.get("success"):
            return {"error": config_result.get("error", "Unknown error"), "function_name": function_name}

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


class LambdaConfigTool(BaseTool):
    """Get Lambda function configuration details (lightweight, no code)."""

    name = "get_lambda_configuration"
    source = "cloudwatch"
    description = "Get Lambda function configuration details (lightweight — no code retrieval)."
    use_cases = [
        "Quick configuration checks for Lambda functions",
        "Environment variable inspection",
        "Timeout and memory settings review",
    ]
    requires = ["function_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "function_name": {"type": "string"},
        },
        "required": ["function_name"],
    }

    def is_available(self, sources: dict) -> bool:
        return _lambda_available(sources)

    def extract_params(self, sources: dict) -> dict:
        return {"function_name": _lambda_name(sources)}

    def run(self, function_name: str, **_kwargs) -> dict:
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


# Backward-compatible aliases
inspect_lambda_function = LambdaInspectTool()
get_lambda_invocation_logs = LambdaInvocationLogsTool()
get_lambda_errors = LambdaErrorsTool()
get_lambda_configuration = LambdaConfigTool()
