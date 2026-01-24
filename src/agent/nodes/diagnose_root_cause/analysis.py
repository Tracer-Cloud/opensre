"""Analysis functions for root cause diagnosis."""


def analyze_logs(all_logs: list[dict]) -> dict:
    """Analyze logs for patterns and failure signals."""
    if not all_logs:
        return {"total_logs": 0, "total_errors": 0, "patterns": [], "critical_signals": [], "summary": "No logs available"}

    # Filter error logs from all logs
    error_logs = [
        log for log in all_logs
        if "error" in str(log.get("log_level", "")).lower()
        or "fail" in str(log.get("message", "")).lower()
    ]

    error_messages = [str(log.get("message", "")) for log in error_logs]
    patterns = _extract_error_patterns(error_messages)
    critical_keywords = ["fatal", "critical", "exception", "traceback", "killed", "oom", "timeout"]
    critical_signals = [
        {"level": log.get("log_level"), "message": str(log.get("message", ""))[:200], "timestamp": log.get("timestamp")}
        for log in all_logs
        if any(kw in str(log.get("message", "")).lower() for kw in critical_keywords)
    ][:10]  # Show more critical signals

    return {
        "total_logs": len(all_logs),
        "total_errors": len(error_logs),
        "patterns": patterns,
        "critical_signals": critical_signals,
        "summary": f"Analyzed {len(all_logs)} total logs ({len(error_logs)} errors) with {len(patterns)} distinct patterns",
    }


def analyze_tools(failed_tools: list[dict], total_tools: int) -> dict:
    """Analyze tool execution patterns."""
    if total_tools == 0:
        return {"total_tools": 0, "failed_tools": 0, "failure_rate": 0.0, "failure_modes": [], "summary": "No tools executed"}

    failure_modes: dict[str, int] = {}
    for tool in failed_tools:
        exit_code = str(tool.get("exit_code", "unknown"))
        failure_modes[f"exit_code_{exit_code}"] = failure_modes.get(f"exit_code_{exit_code}", 0) + 1

    failure_rate = len(failed_tools) / total_tools if total_tools > 0 else 0.0
    return {
        "total_tools": total_tools,
        "failed_tools": len(failed_tools),
        "failure_rate": failure_rate,
        "failure_modes": [{"mode": k, "count": v} for k, v in failure_modes.items()],
        "summary": f"{len(failed_tools)}/{total_tools} tools failed ({failure_rate*100:.1f}% failure rate)",
    }


def analyze_job_failures(failed_jobs: list[dict]) -> dict:
    """Analyze AWS Batch job failure patterns."""
    if not failed_jobs:
        return {"total_failed": 0, "failure_reasons": [], "exit_codes": [], "summary": "No failed jobs to analyze"}

    failure_reasons: dict[str, int] = {}
    exit_codes: dict[int, int] = {}
    for job in failed_jobs:
        failure_reasons[job.get("status_reason", "unknown")] = failure_reasons.get(job.get("status_reason", "unknown"), 0) + 1
        exit_code = job.get("exit_code")
        if exit_code is not None:
            exit_codes[exit_code] = exit_codes.get(exit_code, 0) + 1

    common_reason = max(failure_reasons.items(), key=lambda x: x[1])[0] if failure_reasons else None
    return {
        "total_failed": len(failed_jobs),
        "failure_reasons": [{"reason": k, "count": v} for k, v in failure_reasons.items()],
        "exit_codes": [{"code": k, "count": v} for k, v in exit_codes.items()],
        "common_reason": common_reason,
        "common_exit_code": max(exit_codes.items(), key=lambda x: x[1])[0] if exit_codes else None,
        "summary": f"{len(failed_jobs)} jobs failed. Most common: {common_reason or 'unknown'}",
    }


def analyze_metrics(host_metrics: dict, airflow_metrics: dict) -> dict:
    """Analyze host and Airflow metrics for anomalies."""
    anomalies = []

    metrics_data = host_metrics.get("data", []) if host_metrics.get("success") else []
    if metrics_data:
        max_cpu = max((float(m.get("cpu", 0) or 0) for m in metrics_data), default=0.0)
        max_ram = max((float(m.get("ram", 0) or 0) for m in metrics_data), default=0.0)
        max_disk = max((float(m.get("disk", 0) or 0) for m in metrics_data), default=0.0)

        for threshold, metric_type, value in [(95, "high_cpu", max_cpu), (90, "high_memory", max_ram), (90, "high_disk", max_disk)]:
            if value > threshold:
                anomalies.append({
                    "type": metric_type,
                    "value": value,
                    "threshold": threshold,
                    "severity": "critical" if metric_type != "high_disk" else "warning",
                })

    airflow_connected = airflow_metrics.get("connected", False)
    if airflow_connected:
        tasks = airflow_metrics.get("data", {}).get("tasks", [])
        total_failures = sum(t.get("failures", 0) for t in tasks) if tasks else 0
        if total_failures > 0:
            anomalies.append({"type": "airflow_task_failures", "value": total_failures, "severity": "critical"})

    return {
        "host_metrics_available": host_metrics.get("success", False),
        "airflow_metrics_available": airflow_connected,
        "anomalies": anomalies,
        "summary": f"Found {len(anomalies)} metric anomalies" if anomalies else "No metric anomalies detected",
    }


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


def _extract_error_patterns(messages: list[str]) -> list[dict]:
    """Extract common error patterns from log messages."""
    pattern_map = {
        "timeout": ["timeout"],
        "memory_issue": ["memory", "oom"],
        "permission_error": ["permission", "access denied"],
        "network_error": ["connection", "network"],
        "file_not_found": ["file not found", "no such file"],
        "exit_code_error": ["exit code", "exit_code"],
    }
    patterns = []
    seen_patterns: set[str] = set()

    for pattern_key, keywords in pattern_map.items():
        if any(kw in msg.lower() for msg in messages for kw in keywords) and pattern_key not in seen_patterns:
            seen_patterns.add(pattern_key)
            count = sum(1 for msg in messages if any(kw in msg.lower() for kw in keywords))
            patterns.append({"type": pattern_key, "count": count})

    if not patterns:
        patterns.append({"type": "other_error", "count": len(messages)})

    return patterns


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
