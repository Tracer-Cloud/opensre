"""Tracer run tasks tool."""

from __future__ import annotations

from core.domain.types.tools import ToolSurface
from core.tool_framework import tool
from integrations.tracer import TracerTaskResult, get_tracer_client


@tool(
    name="get_tracer_tasks",
    source="tracer_web",
    description="Get tasks for a specific pipeline run from the Tracer API.",
    use_cases=[
        "Retrieving detailed task information for a pipeline run",
        "Understanding which specific tasks failed or succeeded",
    ],
    requires=["run_id"],
    input_schema={
        "type": "object",
        "properties": {
            "run_id": {
                "type": "string",
                "description": "The unique identifier for the pipeline run",
            },
        },
        "required": ["run_id"],
    },
    is_available=lambda sources: bool(sources.get("tracer_web")),
    surfaces=(ToolSurface.CHAT,),
)
def get_tracer_tasks(run_id: str) -> TracerTaskResult:
    """Get tasks for a specific pipeline run from the Tracer API."""
    client = get_tracer_client()
    return client.get_run_tasks(run_id)
