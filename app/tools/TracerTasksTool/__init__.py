"""Tracer run tasks tool."""

from __future__ import annotations

from app.integrations.clients.tracer_client import TracerTaskResult, get_tracer_client
from app.tools.base import BaseTool
from app.tools.tool_decorator import tool


class TracerTasksTool(BaseTool):
    """Get tasks for a specific pipeline run from the Tracer API."""

    name = "get_tracer_tasks"
    source = "tracer_web"
    description = "Get tasks for a specific pipeline run from the Tracer API."
    use_cases = [
        "Retrieving detailed task information for a pipeline run",
        "Understanding which specific tasks failed or succeeded",
    ]
    requires = ["run_id"]
    input_schema = {
        "type": "object",
        "properties": {
            "run_id": {"type": "string", "description": "The unique identifier for the pipeline run"},
        },
        "required": ["run_id"],
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("tracer_web"))

    def run(self, run_id: str, **_kwargs) -> TracerTaskResult:
        client = get_tracer_client()
        return client.get_run_tasks(run_id)


get_tracer_tasks = TracerTasksTool()
get_tracer_tasks_tool = tool(get_tracer_tasks)
