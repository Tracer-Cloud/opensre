"""Tracer failed tool execution results."""

from __future__ import annotations

from typing import Any

from app.services.tracer_client import get_tracer_web_client
from app.tools.tool_decorator import tool
from app.tools.TracerFailedJobsTool import _tracer_available, _tracer_trace_id


@tool(
    name="get_failed_tools",
    display_name="tool results",
    source="tracer_web",
    description="Get tools that failed during a pipeline execution.",
    use_cases=[
        "Proving tool failure hypothesis",
        "Identifying specific failing components",
        "Understanding error patterns in tool execution",
    ],
    requires=["trace_id"],
    input_schema={
        "type": "object",
        "properties": {
            "trace_id": {"type": "string"},
        },
        "required": ["trace_id"],
    },
    is_available=_tracer_available,
    extract_params=lambda sources: {"trace_id": _tracer_trace_id(sources)},
    surfaces=("investigation", "chat"),
)
def get_failed_tools(trace_id: str) -> dict[str, Any]:
    """Get tools that failed during a pipeline execution."""
    if not trace_id:
        return {"error": "trace_id is required"}

    client = get_tracer_web_client()
    tools_data = client.get_tools(trace_id)
    tool_list = tools_data.get("data", [])

    failed_tools = [
        {
            "tool_name": t.get("tool_name"),
            "exit_code": t.get("exit_code"),
            "reason": t.get("reason"),
            "explanation": t.get("explanation"),
        }
        for t in tool_list
        if t.get("exit_code") and str(t.get("exit_code")) != "0"
    ]

    return {
        "failed_tools": failed_tools,
        "total_tools": len(tool_list),
        "failed_count": len(failed_tools),
        "source": "tools/[traceId] API",
    }
