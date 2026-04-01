"""Tracer runtime log tool."""

from __future__ import annotations

from typing import Any

from app.tools.clients.tracer_client import get_tracer_web_client
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_decorator import tool


class TracerErrorLogsTool(BaseTool):
    """Get logs from OpenSearch, optionally filtered for errors."""

    name = "get_error_logs"
    source = "tracer_web"
    description = "Get logs from OpenSearch, optionally filtered for errors."
    use_cases = [
        "Proving error pattern hypothesis",
        "Finding root cause error messages",
        "Understanding failure timeline",
    ]
    requires = ["trace_id"]
    input_schema = {
        "type": "object",
        "properties": {
            "trace_id": {"type": "string"},
            "size": {"type": "integer", "default": 500},
            "error_only": {"type": "boolean", "default": True},
        },
        "required": ["trace_id"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("tracer_web", {}).get("trace_id"))

    def extract_params(self, sources: dict) -> dict:
        return {
            "trace_id": sources.get("tracer_web", {}).get("trace_id"),
            "size": 500,
            "error_only": True,
        }

    def run(self, trace_id: str, size: int = 500, error_only: bool = True, **_kwargs: Any) -> dict:
        if not trace_id:
            return {"error": "trace_id is required"}

        client = get_tracer_web_client()
        logs_data = client.get_logs(run_id=trace_id, size=size)

        if not isinstance(logs_data, dict):
            logs_data = {"data": [], "success": False}
        if "data" not in logs_data:
            logs_data = {"data": logs_data if isinstance(logs_data, list) else [], "success": True}

        log_list = logs_data.get("data", [])

        if error_only:
            filtered_logs = [
                {
                    "message": log.get("message", "")[:500],
                    "log_level": log.get("log_level"),
                    "timestamp": log.get("timestamp"),
                }
                for log in log_list
                if "error" in str(log.get("log_level", "")).lower()
                or "fail" in str(log.get("message", "")).lower()
            ][:50]
        else:
            filtered_logs = [
                {
                    "message": log.get("message", "")[:500],
                    "log_level": log.get("log_level"),
                    "timestamp": log.get("timestamp"),
                }
                for log in log_list
            ][:200]

        return {
            "logs": filtered_logs,
            "total_logs": len(log_list),
            "filtered_count": len(filtered_logs),
            "error_only": error_only,
            "source": "opensearch/logs API",
        }


# Backward-compatible aliases
get_error_logs = TracerErrorLogsTool()
get_error_logs_tool = tool(get_error_logs)
