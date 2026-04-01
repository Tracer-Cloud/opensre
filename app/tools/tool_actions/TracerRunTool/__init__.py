"""Tracer latest run tool."""

from __future__ import annotations

from app.tools.clients.tracer_client import TracerRunResult, get_tracer_client
from app.tools.tool_actions.base import BaseTool
from app.tools.tool_decorator import tool


class TracerRunTool(BaseTool):
    """Get the latest pipeline run from the Tracer API."""

    name = "get_tracer_run"
    source = "tracer_web"
    description = "Get the latest pipeline run from the Tracer API."
    use_cases = [
        "Retrieving the most recent run information for a Tracer pipeline",
        "Checking current pipeline run status and metadata",
    ]
    requires = []
    input_schema = {
        "type": "object",
        "properties": {
            "pipeline_name": {"type": "string"},
        },
        "required": [],
    }

    def run(self, pipeline_name: str | None = None, **_kwargs) -> TracerRunResult:
        client = get_tracer_client()
        return client.get_latest_run(pipeline_name)


get_tracer_run = TracerRunTool()
get_tracer_run_tool = tool(get_tracer_run)
