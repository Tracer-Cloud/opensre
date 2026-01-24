"""Evidence gathering - dynamic runtime data that proves/disproves hypotheses."""


from src.agent.tools.tracer_client import get_tracer_web_client


def gather_evidence_for_trace(trace_id: str, context: dict) -> dict:  # noqa: ARG001
    """
    Gather evidence (runtime data) for a specific trace.

    This includes:
    - Failed jobs (proves job failure hypothesis)
    - Failed tools (proves tool failure hypothesis)
    - Error logs (proves error pattern hypothesis)
    - Metrics anomalies (proves resource constraint hypothesis)
    - Batch statistics (proves systemic failure hypothesis)

    Does NOT include:
    - Pipeline metadata (context)
    - Run summary (context)
    - User/instance info (context)
    """
    if not trace_id:
        return {}

    client = get_tracer_web_client()

    # Gather detailed investigation data (runtime evidence)
    batch_details = client.get_batch_details(trace_id)
    tools_data = client.get_tools(trace_id)
    batch_jobs = client.get_batch_jobs(trace_id, ["FAILED", "SUCCEEDED"], return_dict=True)

    # Fetch logs - try with trace_id as runId parameter
    logs_data = client.get_logs(run_id=trace_id, size=500)  # Fetch more logs for comprehensive analysis
    # Handle API response structure - ensure data key exists
    if not isinstance(logs_data, dict):
        logs_data = {"data": [], "success": False}
    if "data" not in logs_data:
        logs_data = {"data": logs_data if isinstance(logs_data, list) else [], "success": True}

    host_metrics = client.get_host_metrics(trace_id)
    airflow_metrics = client.get_airflow_metrics(trace_id)

    # Extract failed tools (evidence)
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

    # Extract failed jobs (evidence)
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

    # Extract logs (evidence)
    log_list = logs_data.get("data", [])
    total_logs = len(log_list)

    # Extract error logs (filtered)
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

    # Also extract all logs (not just errors) for investigation
    all_logs = [
        {
            "message": log.get("message", "")[:500],
            "log_level": log.get("log_level"),
            "timestamp": log.get("timestamp"),
        }
        for log in log_list
    ][:200]  # Store up to 200 logs for comprehensive analysis

    batch_stats = batch_details.get("stats", {})

    # Return only evidence (runtime data)
    evidence = {
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
        "all_logs": all_logs,
        "total_logs": total_logs,
        "error_logs_source": "opensearch/logs API",
        "host_metrics": host_metrics,
        "host_metrics_source": "runs/[trace_id]/host-metrics API",
        "airflow_metrics": airflow_metrics,
        "airflow_metrics_source": "runs/[trace_id]/airflow API",
        "total_tools": len(tool_list),
        "total_jobs": len(job_list),
        "logs_available": total_logs > 0,
    }

    return evidence


def gather_evidence_for_context(context: dict) -> dict:
    """
    Gather evidence based on available context.

    Extracts trace_id from context and gathers runtime evidence.
    """
    # Try to get trace_id from tracer_web_run context
    tracer_web_run = context.get("tracer_web_run", {})
    trace_id = tracer_web_run.get("trace_id")

    if not trace_id:
        # Fallback to pipeline_run context
        pipeline_run = context.get("pipeline_run", {})
        trace_id = pipeline_run.get("run_id")

    if not trace_id:
        return {}

    return gather_evidence_for_trace(trace_id, context)
