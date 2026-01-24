"""Collect evidence from external systems."""

import logging
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from src.agent.nodes.publish_findings.render import render_evidence
from src.agent.state import EvidenceSource, InvestigationState
from src.agent.tools.tools import check_s3_marker, get_batch_jobs, get_tracer_run
from src.agent.tools.tracer_web_client import get_tracer_web_client

logger = logging.getLogger(__name__)

TIMEOUT = 10.0


def _call_safe(fn, **kwargs) -> tuple[Any, str | None]:
    """Call function with timeout. Returns (result, error)."""
    with ThreadPoolExecutor(max_workers=1) as ex:
        try:
            return ex.submit(fn, **kwargs).result(timeout=TIMEOUT), None
        except FuturesTimeoutError:
            return None, f"Timeout after {TIMEOUT}s"
        except Exception as e:
            return None, str(e)


def _collect_tracer() -> dict:
    result, err = _call_safe(get_tracer_run)
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


def _collect_storage() -> dict:
    result, err = _call_safe(check_s3_marker, bucket="tracer-logs", prefix="events/")
    if err:
        return {"found": False, "error": err}
    return {"found": True, "marker_exists": result.marker_exists, "file_count": result.file_count, "files": result.files}


def _collect_batch() -> dict:
    result, err = _call_safe(get_batch_jobs)
    if err or not result.found:
        return {"found": False, "error": err or "No jobs found"}
    return {
        "found": True,
        "total_jobs": result.total_jobs,
        "succeeded_jobs": result.succeeded_jobs,
        "failed_jobs": result.failed_jobs,
        "failure_reason": result.failure_reason,
        "jobs": result.jobs,
    }


def _collect_tracer_web_failed_run() -> dict:
    result, err = _call_safe(_fetch_tracer_web_failed_run)
    if err:
        return {"found": False, "error": err}
    return result


def _fetch_tracer_web_failed_run() -> dict:
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
        "pipelines_checked": len(pipelines),
    }


COLLECTORS: dict[EvidenceSource, tuple[callable, str]] = {
    "tracer": (_collect_tracer, "pipeline_run"),
    "storage": (_collect_storage, "s3"),
    "batch": (_collect_batch, "batch_jobs"),
    "tracer_web": (_collect_tracer_web_failed_run, "tracer_web_run"),
}


def node_collect_evidence(state: InvestigationState) -> dict:
    """Execute plan by calling tool functions. Fails soft."""
    evidence = {}
    for source in state.get("plan_sources", []):
        if source in COLLECTORS:
            fn, key = COLLECTORS[source]
            evidence[key] = fn()
    render_evidence(evidence)
    return {"evidence": evidence}

