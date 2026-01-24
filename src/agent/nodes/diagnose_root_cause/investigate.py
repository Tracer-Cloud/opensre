"""Deep multi-source investigation for root cause analysis."""

from src.agent.nodes.diagnose_root_cause.analysis import (
    analyze_job_failures,
    analyze_logs,
    analyze_metrics,
    analyze_tools,
    build_causal_chain,
)
from src.agent.state import InvestigationState


def perform_deep_investigation(state: InvestigationState) -> dict:
    """
    Perform multi-source investigation with deep analysis.

    Returns:
        dict with investigation results including:
        - evidence_sources_checked: List of sources that were queried
        - evidence_sources_skipped: List of sources that were unavailable
        - tools_executed: List of tools/APIs that were called
        - logs_analysis: Analysis of log patterns
        - metrics_analysis: Analysis of metrics anomalies
        - causal_chain: Inferred causal chain from evidence
    """
    evidence = state.get("evidence", {})
    web_run = evidence.get("tracer_web_run", {})

    investigation = {
        "evidence_sources_checked": [],
        "evidence_sources_skipped": [],
        "tools_executed": [],
        "logs_analysis": {},
        "metrics_analysis": {},
        "causal_chain": [],
    }

    # Track which evidence sources were checked
    source_map = {
        "pipeline_run": ("tracer_pipeline_api", "get_tracer_run"),
        "s3": ("s3_storage", "check_s3_marker"),
        "batch_jobs": ("aws_batch_jobs", "get_batch_jobs"),
    }
    for key, (source_name, tool_name) in source_map.items():
        if evidence.get(key, {}).get("found"):
            investigation["evidence_sources_checked"].append(source_name)
            investigation["tools_executed"].append(tool_name)
        else:
            investigation["evidence_sources_skipped"].append(source_name)

    # Deep investigation of web run if available
    if web_run.get("found"):
        investigation["evidence_sources_checked"].append("tracer_web_app")
        investigation["tools_executed"].extend([
            "get_pipelines",
            "get_pipeline_runs",
            "get_batch_details",
            "get_tools",
            "get_batch_jobs",
            "get_logs",
            "get_host_metrics",
            "get_airflow_metrics",
        ])

        # Perform all analyses
        # Use all_logs if available, otherwise fall back to error_logs
        all_logs = web_run.get("all_logs", [])
        error_logs = web_run.get("error_logs", [])
        logs_to_analyze = all_logs if all_logs else error_logs
        logs_analysis = analyze_logs(logs_to_analyze)
        tools_analysis = analyze_tools(web_run.get("failed_tools", []), web_run.get("total_tools", 0))
        jobs_analysis = analyze_job_failures(web_run.get("failed_jobs", []))
        metrics_analysis = analyze_metrics(web_run.get("host_metrics", {}), web_run.get("airflow_metrics", {}))

        investigation["logs_analysis"] = logs_analysis
        total_logs = web_run.get("total_logs", 0)
        investigation["logs_analyzed"] = total_logs
        investigation["error_logs_count"] = len(error_logs)
        investigation["tools_analysis"] = tools_analysis
        investigation["jobs_analysis"] = jobs_analysis
        investigation["metrics_analysis"] = metrics_analysis
        investigation["causal_chain"] = build_causal_chain(web_run, logs_analysis, tools_analysis, jobs_analysis, metrics_analysis)
    else:
        investigation["evidence_sources_skipped"].append("tracer_web_app")

    return investigation
