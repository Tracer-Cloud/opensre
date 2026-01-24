"""Tracer Web App context fetching helpers."""

from __future__ import annotations

import os
from typing import Iterable

from src.agent.constants import TRACER_BASE_URL
from src.agent.nodes.frame_problem.models import TracerWebRunContext
from src.agent.tools.clients.tracer_client import PipelineRunSummary, get_tracer_web_client
from src.agent.utils.auth import extract_org_slug_from_jwt


FAILED_STATUSES = ("failed", "error")


def build_tracer_run_url(pipeline_name: str, trace_id: str | None) -> str | None:
    """Build the correct Tracer run URL with organization slug."""
    if not trace_id:
        return None

    jwt_token = os.getenv("JWT_TOKEN")
    org_slug = None
    if jwt_token:
        org_slug = extract_org_slug_from_jwt(jwt_token)

    if org_slug:
        return f"{TRACER_BASE_URL}/{org_slug}/pipelines/{pipeline_name}/batch/{trace_id}"
    return f"{TRACER_BASE_URL}/pipelines/{pipeline_name}/batch/{trace_id}"


def fetch_failed_run_context(pipeline_name: str | None = None) -> TracerWebRunContext:
    """Fetch context (metadata) about a failed run from Tracer Web App."""
    client = get_tracer_web_client()
    pipeline_names = _list_pipeline_names(client, pipeline_name)

    failed_run = _find_failed_run(client, pipeline_names)
    if not failed_run:
        return TracerWebRunContext(
            found=False,
            error="No failed runs found",
            pipelines_checked=len(pipeline_names),
        )

    run_url = build_tracer_run_url(failed_run.pipeline_name, failed_run.trace_id)
    return TracerWebRunContext(
        found=True,
        pipeline_name=failed_run.pipeline_name,
        run_id=failed_run.run_id,
        run_name=failed_run.run_name,
        trace_id=failed_run.trace_id,
        status=failed_run.status,
        start_time=failed_run.start_time,
        end_time=failed_run.end_time,
        run_cost=failed_run.run_cost,
        tool_count=failed_run.tool_count,
        user_email=failed_run.user_email,
        instance_type=failed_run.instance_type,
        region=failed_run.region,
        log_file_count=failed_run.log_file_count,
        run_url=run_url,
        pipelines_checked=len(pipeline_names),
    )


def _list_pipeline_names(client, pipeline_name: str | None) -> list[str]:
    if pipeline_name:
        return [pipeline_name]
    pipelines = client.get_pipelines(page=1, size=50)
    return [pipeline.pipeline_name for pipeline in pipelines if pipeline.pipeline_name]


def _find_failed_run(client, pipeline_names: Iterable[str]) -> PipelineRunSummary | None:
    for name in pipeline_names:
        runs = client.get_pipeline_runs(name, page=1, size=50)
        for run in runs:
            status = (run.status or "").lower()
            if status in FAILED_STATUSES:
                return run
    return None
