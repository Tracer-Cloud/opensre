"""Tracer failed run discovery tool."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from app.config import get_tracer_base_url
from app.services.tracer_client import (
    PipelineRunSummary,
    get_tracer_web_client,
)
from app.tools.tool_decorator import tool

FAILED_STATUSES = ("failed", "error")


def build_tracer_run_url(pipeline_name: str, trace_id: str | None, org_slug: str | None = None) -> str | None:
    """Build Tracer run URL using the organization slug from the client."""
    if not trace_id:
        return None
    base = get_tracer_base_url()
    return (
        f"{base}/{org_slug}/pipelines/{pipeline_name}/batch/{trace_id}"
        if org_slug
        else f"{base}/pipelines/{pipeline_name}/batch/{trace_id}"
    )


def _list_pipeline_names(client: Any, pipeline_name: str | None) -> list[str]:
    if pipeline_name:
        return [pipeline_name]
    pipelines = client.get_pipelines(page=1, size=50)
    return [p.pipeline_name for p in pipelines if p.pipeline_name]


def _find_failed_run(client: Any, pipeline_names: Iterable[str]) -> PipelineRunSummary | None:
    for name in pipeline_names:
        runs = client.get_pipeline_runs(name, page=1, size=50)
        for run in runs:
            if not isinstance(run, PipelineRunSummary):
                continue
            if (run.status or "").lower() in FAILED_STATUSES:
                return run
    return None


@tool(
    name="fetch_failed_run",
    source="tracer_web",
    description="Fetch context (metadata) about a failed run from the Tracer Web App.",
    use_cases=[
        "Getting details of the most recent failed pipeline run",
        "Finding the trace_id needed for deeper investigation",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "pipeline_name": {"type": "string", "description": "Optional pipeline name to filter runs"},
        },
        "required": [],
    },
    is_available=lambda sources: bool(sources.get("tracer_web")),
    surfaces=("investigation", "chat"),
)
def fetch_failed_run(pipeline_name: str | None = None) -> dict[str, Any]:
    """Fetch context (metadata) about a failed run from Tracer Web App."""
    client = get_tracer_web_client()
    pipeline_names = _list_pipeline_names(client, pipeline_name)
    failed_run = _find_failed_run(client, pipeline_names)

    if not failed_run:
        return {
            "found": False,
            "error": "No failed runs found",
            "pipelines_checked": len(pipeline_names),
        }

    run_url = build_tracer_run_url(failed_run.pipeline_name, failed_run.trace_id, client.organization_slug)
    return {
        "found": True,
        "pipeline_name": failed_run.pipeline_name,
        "run_id": failed_run.run_id,
        "run_name": failed_run.run_name,
        "trace_id": failed_run.trace_id,
        "status": failed_run.status,
        "start_time": failed_run.start_time,
        "end_time": failed_run.end_time,
        "run_cost": failed_run.run_cost,
        "tool_count": failed_run.tool_count,
        "user_email": failed_run.user_email,
        "instance_type": failed_run.instance_type,
        "region": failed_run.region,
        "log_file_count": failed_run.log_file_count,
        "run_url": run_url,
        "pipelines_checked": len(pipeline_names),
    }
