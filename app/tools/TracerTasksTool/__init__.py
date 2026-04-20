"""Tracer run tasks tool."""

from __future__ import annotations

from app.services.tracer_client import TracerTaskResult, get_tracer_client
from app.tools.tool_decorator import tool


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
    surfaces=("investigation", "chat"),
)
def get_tracer_tasks(run_id: str) -> TracerTaskResult:
    """Get tasks for a specific pipeline run from the Tracer API."""
    client = get_tracer_client()
    return client.get_run_tasks(run_id)
