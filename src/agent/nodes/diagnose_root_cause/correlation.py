"""Correlation functions for building causal chains from evidence."""


def build_causal_chain(
    web_run: dict,
    logs_analysis: dict,
    tools_analysis: dict,
    jobs_analysis: dict,
    metrics_analysis: dict,
) -> list[dict]:
    """Build a causal chain from multiple evidence sources."""
    chain = []

    for job in web_run.get("failed_jobs", [])[:3]:
        chain.append({
            "event_type": "job_failure",
            "job_name": job.get("job_name"),
            "exit_code": job.get("exit_code"),
            "status_reason": job.get("status_reason"),
            "evidence_source": "aws_batch_jobs",
        })

    for tool in web_run.get("failed_tools", [])[:3]:
        chain.append({
            "event_type": "tool_failure",
            "tool_name": tool.get("tool_name"),
            "exit_code": tool.get("exit_code"),
            "reason": tool.get("reason"),
            "evidence_source": "tracer_tools",
        })

    for signal in logs_analysis.get("critical_signals", [])[:2]:
        chain.append({
            "event_type": "log_signal",
            "level": signal.get("level"),
            "message_preview": signal.get("message", "")[:100],
            "evidence_source": "tracer_logs",
        })

    for anomaly in metrics_analysis.get("anomalies", [])[:2]:
        chain.append({
            "event_type": "metric_anomaly",
            "anomaly_type": anomaly.get("type"),
            "value": anomaly.get("value"),
            "severity": anomaly.get("severity"),
            "evidence_source": "host_metrics" if "cpu" in str(anomaly.get("type")) or "memory" in str(anomaly.get("type")) else "airflow_metrics",
        })

    if chain:
        correlation = _correlate_events(chain, jobs_analysis, tools_analysis, metrics_analysis)
        if correlation:
            chain.append({"event_type": "correlation", "analysis": correlation, "evidence_source": "synthesis"})

    return chain


def _correlate_events(
    chain: list[dict],
    jobs_analysis: dict,
    tools_analysis: dict,
    metrics_analysis: dict,
) -> str | None:
    """Correlate events across evidence sources to identify root cause."""
    correlations = []

    job_exit_codes = {e.get("exit_code") for e in chain if e.get("event_type") == "job_failure"}
    tool_exit_codes = {e.get("exit_code") for e in chain if e.get("event_type") == "tool_failure"}
    if job_exit_codes & tool_exit_codes:
        correlations.append(f"Exit codes {job_exit_codes & tool_exit_codes} appear in both job and tool failures")

    failure_rate = tools_analysis.get("failure_rate", 0.0)
    if failure_rate > 0.5:
        correlations.append(f"High tool failure rate ({failure_rate*100:.1f}%) suggests systemic issue")

    common_reason = jobs_analysis.get("common_reason")
    if common_reason and "container" in str(common_reason).lower():
        correlations.append(f"Container-level failure pattern: {common_reason}")

    for anomaly_type in ["high_memory", "high_cpu"]:
        anomaly = next((a for a in metrics_analysis.get("anomalies", []) if a.get("type") == anomaly_type), None)
        if anomaly:
            msg = f"High {anomaly_type.replace('_', ' ')} ({anomaly.get('value')}%)"
            msg += " may have caused container termination" if anomaly_type == "high_memory" else " suggests resource contention"
            correlations.append(msg)

    return "; ".join(correlations) if correlations else None
