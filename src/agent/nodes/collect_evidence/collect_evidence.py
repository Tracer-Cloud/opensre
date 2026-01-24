"""Collect evidence from external systems."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FuturesTimeoutError
from typing import Any

from src.agent.nodes.publish_findings.render import render_evidence
from src.agent.state import EvidenceSource, InvestigationState
from src.agent.tools.tools import check_s3_marker, get_batch_jobs, get_tracer_run
from src.agent.tools.tracer_client import get_tracer_web_client

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


def _investigate_trace_id(trace_id: str) -> dict:
    """Investigate a specific trace_id in detail."""
    client = get_tracer_web_client()

    # Get run summary first
    pipelines = client.get_pipelines(page=1, size=50)
    failed_run = None
    for pipeline in pipelines:
        runs = client.get_pipeline_runs(pipeline.pipeline_name, page=1, size=50)
        for run in runs:
            if run.trace_id == trace_id:
                failed_run = run
                break
        if failed_run:
            break

    if not failed_run:
        return {
            "found": False,
            "error": f"Trace {trace_id} not found",
        }

    # Gather detailed investigation data
    batch_details = client.get_batch_details(trace_id)
    tools_data = client.get_tools(trace_id)
    batch_jobs = client.get_batch_jobs(trace_id, ["FAILED", "SUCCEEDED"], return_dict=True)
    logs_data = client.get_logs(run_id=trace_id, size=100)

    # Extract failed tools
    tool_list = tools_data.get("data", [])
    failed_tools = [
        {
            "tool_name": t.get("tool_name"),
            "exit_code": t.get("exit_code"),
            "reason": t.get("reason"),
            "explanation": t.get("explanation"),
        }
        for t in tool_list
        if t.get("exit_code") and str(t.get("exit_code")) != "0"
    ]

    # Extract failed jobs
    job_list = batch_jobs.get("data", [])
    failed_jobs = []
    for job in job_list:
        if job.get("status") == "FAILED":
            container = job.get("container", {})
            failed_jobs.append({
                "job_name": job.get("jobName"),
                "status_reason": job.get("statusReason"),
                "container_reason": container.get("reason") if isinstance(container, dict) else None,
                "exit_code": container.get("exitCode") if isinstance(container, dict) else None,
            })

    # Extract error logs
    log_list = logs_data.get("data", [])
    error_logs = [
        {
            "message": log.get("message", "")[:500],
            "log_level": log.get("log_level"),
            "timestamp": log.get("timestamp"),
        }
        for log in log_list
        if "error" in str(log.get("log_level", "")).lower()
        or "fail" in str(log.get("message", "")).lower()
    ][:10]  # Limit to 10 most recent

    batch_stats = batch_details.get("stats", {})

    # Construct run URL
    base_url = os.getenv("TRACER_WEB_APP_URL", "http://localhost:3000")
    run_url = f"{base_url}/pipelines/{failed_run.pipeline_name}/batch/{trace_id}"

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
        # Detailed investigation data with source attribution
        "batch_stats": {
            "failed_job_count": batch_stats.get("failed_job_count", 0),
            "total_runs": batch_stats.get("total_runs", 0),
            "total_cost": batch_stats.get("total_cost", 0),
            "source": "batch-runs/[trace_id] API",
        },
        "failed_tools": failed_tools,
        "failed_tools_source": "tools/[traceId] API",
        "failed_jobs": failed_jobs,
        "failed_jobs_source": "aws/batch/jobs/completed API",
        "error_logs": error_logs,
        "error_logs_source": "opensearch/logs API",
        "total_tools": len(tool_list),
        "total_jobs": len(job_list),
        "logs_available": len(log_list) > 0,
    }


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

    trace_id = failed_run.trace_id
    if not trace_id:
        return {
            "found": True,
            "pipeline_name": failed_run.pipeline_name,
            "run_id": failed_run.run_id,
            "run_name": failed_run.run_name,
            "trace_id": None,
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

    # Gather detailed investigation data
    batch_details = client.get_batch_details(trace_id)
    tools_data = client.get_tools(trace_id)
    batch_jobs = client.get_batch_jobs(trace_id, ["FAILED", "SUCCEEDED"], return_dict=True)
    logs_data = client.get_logs(run_id=trace_id, size=100)

    # Extract failed tools
    tool_list = tools_data.get("data", [])
    failed_tools = [
        {
            "tool_name": t.get("tool_name"),
            "exit_code": t.get("exit_code"),
            "reason": t.get("reason"),
            "explanation": t.get("explanation"),
        }
        for t in tool_list
        if t.get("exit_code") and str(t.get("exit_code")) != "0"
    ]

    # Extract failed jobs
    job_list = batch_jobs.get("data", [])
    failed_jobs = []
    for job in job_list:
        if job.get("status") == "FAILED":
            container = job.get("container", {})
            failed_jobs.append({
                "job_name": job.get("jobName"),
                "status_reason": job.get("statusReason"),
                "container_reason": container.get("reason") if isinstance(container, dict) else None,
                "exit_code": container.get("exitCode") if isinstance(container, dict) else None,
            })

    # Extract error logs
    log_list = logs_data.get("data", [])
    error_logs = [
        {
            "message": log.get("message", "")[:500],
            "log_level": log.get("log_level"),
            "timestamp": log.get("timestamp"),
        }
        for log in log_list
        if "error" in str(log.get("log_level", "")).lower()
        or "fail" in str(log.get("message", "")).lower()
    ][:10]  # Limit to 10 most recent

    batch_stats = batch_details.get("stats", {})

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
        "pipelines_checked": len(pipelines),
        # Construct run URL
        "run_url": f"{os.getenv('TRACER_WEB_APP_URL', 'http://localhost:3000')}/pipelines/{failed_run.pipeline_name}/batch/{trace_id}",
        # Detailed investigation data with source attribution
        "batch_stats": {
            "failed_job_count": batch_stats.get("failed_job_count", 0),
            "total_runs": batch_stats.get("total_runs", 0),
            "total_cost": batch_stats.get("total_cost", 0),
            "source": "batch-runs/[trace_id] API",
        },
        "failed_tools": failed_tools,
        "failed_tools_source": "tools/[traceId] API",
        "failed_jobs": failed_jobs,
        "failed_jobs_source": "aws/batch/jobs/completed API",
        "error_logs": error_logs,
        "error_logs_source": "opensearch/logs API",
        "total_tools": len(tool_list),
        "total_jobs": len(job_list),
        "logs_available": len(log_list) > 0,
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
