"""Context building - information that could exist before the incident."""

import base64
import json
import os

from src.agent.context.utils import call_safe
from src.agent.tools.tools import get_tracer_run
from src.agent.tools.tracer_client import get_tracer_web_client


def build_context_tracer() -> dict:
    """Build context from Tracer staging API (metadata)."""
    result, err = call_safe(get_tracer_run)
    if err or not result.found:
        return {"found": False, "error": err or "No runs found"}
    return {
        "found": True,
        "run_id": result.run_id,
        "pipeline_name": result.pipeline_name,
        "run_name": result.run_name,
        "status": result.status,
        "run_time_minutes": round(result.run_time_seconds / 60, 1) if result.run_time_seconds else 0,
        "run_cost_usd": round(result.run_cost, 2) if result.run_cost else 0,
        "max_ram_gb": round(result.max_ram_gb, 1) if result.max_ram_gb else 0,
        "user_email": result.user_email,
        "team": result.team,
        "instance_type": result.instance_type,
    }


def build_context_tracer_web() -> dict:
    """Build context from Tracer Web App (metadata about failed run)."""
    result, err = call_safe(_fetch_tracer_web_run_context)
    if err:
        return {"found": False, "error": err}
    return result


def _extract_org_slug_from_jwt(jwt_token: str) -> str | None:
    """Extract organization slug from JWT token."""
    try:
        parts = jwt_token.split(".")
        if len(parts) < 2:
            return None
        # Decode JWT payload (add padding if needed)
        payload_b64 = parts[1]
        payload_b64 += "=" * (4 - len(payload_b64) % 4)  # Add padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("organization_slug")
    except Exception:
        return None


def build_tracer_run_url(pipeline_name: str, trace_id: str | None) -> str | None:
    """Build the correct Tracer run URL with organization slug."""
    if not trace_id:
        return None

    base_url = os.getenv("TRACER_WEB_APP_URL", "http://localhost:3000")
    # Extract org slug from JWT token
    jwt_token = os.getenv("JWT_TOKEN")
    org_slug = None
    if jwt_token:
        org_slug = _extract_org_slug_from_jwt(jwt_token)

    if org_slug:
        return f"{base_url}/{org_slug}/pipelines/{pipeline_name}/batch/{trace_id}"
    # Fallback to old format if org slug not available
    return f"{base_url}/pipelines/{pipeline_name}/batch/{trace_id}"


def _fetch_tracer_web_run_context() -> dict:
    """Fetch context (metadata) about a failed run from Tracer Web App."""
    client = get_tracer_web_client()
    pipelines = client.get_pipelines(page=1, size=50)

    failed_run = None
    for pipeline in pipelines:
        runs = client.get_pipeline_runs(pipeline.pipeline_name, page=1, size=50)
        for run in runs:
            status = (run.status or "").lower()
            if status in ("failed", "error"):
                failed_run = run
                break
        if failed_run:
            break

    if not failed_run:
        return {
            "found": False,
            "error": "No failed runs found",
            "pipelines_checked": len(pipelines),
        }

    trace_id = failed_run.trace_id
    run_url = build_tracer_run_url(failed_run.pipeline_name, trace_id)

    return {
        "found": True,
        "pipeline_name": failed_run.pipeline_name,
        "run_id": failed_run.run_id,
        "run_name": failed_run.run_name,
        "trace_id": trace_id,
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
        "pipelines_checked": len(pipelines),
    }


CONTEXT_BUILDERS: dict[str, tuple[callable, str]] = {
    "tracer": (build_context_tracer, "pipeline_run"),
    "tracer_web": (build_context_tracer_web, "tracer_web_run"),
}


def build_investigation_context(state: dict) -> dict:
    """
    Build investigation context (metadata that could exist before incident).

    This includes:
    - Pipeline metadata (name, run name, trace_id)
    - System metadata (instance type, region, user)
    - Run summary (status, cost, tool count)
    - URLs and identifiers

    Does NOT include:
    - Failed jobs/tools (runtime evidence)
    - Error logs (runtime evidence)
    - Metrics (runtime evidence)
    """
    context = {}
    plan_sources = state.get("plan_sources", [])

    for source in plan_sources:
        if source in CONTEXT_BUILDERS:
            fn, key = CONTEXT_BUILDERS[source]
            context[key] = fn()

    return context
