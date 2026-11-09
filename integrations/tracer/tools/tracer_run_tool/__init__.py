"""Tracer latest run tool."""

from __future__ import annotations

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.tracer import TracerRunResult, get_tracer_client


@tool(
    name="get_tracer_run",
    source="tracer_web",
    description="Get the latest pipeline run from the Tracer API.",
    use_cases=[
        "Retrieving the most recent run information for a Tracer pipeline",
        "Checking current pipeline run status and metadata",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_name": {"type": "string"},
        },
        "required": [],
    },
    is_available=lambda sources: bool(sources.get("tracer_web")),
    surfaces=(ToolSurface.CHAT,),
)
def get_tracer_run(pipeline_name: str | None = None) -> TracerRunResult:
    """Get the latest pipeline run from the Tracer API."""
    client = get_tracer_client()
    return client.get_latest_run(pipeline_name)
