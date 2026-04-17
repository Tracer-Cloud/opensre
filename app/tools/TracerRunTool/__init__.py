"""Tracer latest run tool."""

from __future__ import annotations

from app.services.tracer_client import TracerRunResult, get_tracer_client
from app.tools.tool_decorator import tool


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
    surfaces=("investigation", "chat"),
)
def get_tracer_run(pipeline_name: str | None = None) -> TracerRunResult:
    """Get the latest pipeline run from the Tracer API."""
    client = get_tracer_client()
    return client.get_latest_run(pipeline_name)
