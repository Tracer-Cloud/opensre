"""Prompt building for root cause diagnosis."""

from src.agent.state import InvestigationState


def _format_evidence_summary(evidence: dict) -> dict:
    """Format evidence into summary strings with data quality checks."""
    s3 = evidence.get("s3", {})
    s3_info = (
        f"- Marker: {s3.get('marker_exists')}, Files: {s3.get('file_count', 0)}"
        if s3.get("found")
        else "No S3 data"
    )

    run = evidence.get("pipeline_run", {})
    run_info = "No pipeline data"
    if run.get("found"):
        run_info = f"""- Pipeline: {run.get("pipeline_name")} | Status: {run.get("status")}
- Duration: {run.get("run_time_minutes", 0)}min | Cost: ${run.get("run_cost_usd", 0)}
- User: {run.get("user_email")} | Team: {run.get("team")}"""

    batch = evidence.get("batch_jobs", {})
    batch_info = "No batch data"
    if batch.get("found"):
        batch_info = f"- Jobs: {batch.get('total_jobs')} total, {batch.get('failed_jobs')} failed"
        if batch.get("failure_reason"):
            batch_info += f"\n- Failure: {batch['failure_reason']}"

    web_run = evidence.get("tracer_web_run", {})
    web_run_info = "No web app run data"
    data_quality_warnings = []

    if web_run.get("found"):
        web_run_info = (
            f"- Pipeline: {web_run.get('pipeline_name')} | Status: {web_run.get('status')}\n"
            f"- Run: {web_run.get('run_name')} | Trace: {web_run.get('trace_id')}\n"
            f"- Cost: ${web_run.get('run_cost', 0)} | User: {web_run.get('user_email')}\n"
            f"- Instance: {web_run.get('instance_type')}"
        )

        # Check for host metrics and data quality issues in web_run
        host_metrics = web_run.get("host_metrics", {})
        if host_metrics and isinstance(host_metrics, dict):
            # Check for data quality issues
            data_quality_issues = host_metrics.get("data_quality_issues", [])
            if data_quality_issues:
                for issue in data_quality_issues:
                    field = issue.get("field", "unknown")
                    raw_value = issue.get("raw_value", "unknown")
                    explanation = issue.get("explanation", "")
                    severity = issue.get("severity", "warning")
                    data_quality_warnings.append(
                        f"[{severity.upper()}] {field}: {raw_value} - {explanation[:200]}"
                    )

            # Check for interpretation hints in the metrics data
            metrics_data = host_metrics.get("data", [])
            if isinstance(metrics_data, list):
                for point in metrics_data:
                    if isinstance(point, dict) and "ram_interpretation" in point:
                        # Check for ram_interpretation
                        interp = point["ram_interpretation"]
                        likely_gb = interp.get("likely_value_gb", "unknown")
                        data_quality_warnings.append(
                            f"[INTERPRETATION] RAM value {point.get('ram_raw', 'unknown')} "
                            f"labeled as percent is likely {likely_gb} GB of memory used"
                        )

    # Also check top-level host_metrics in evidence
    top_level_host_metrics = evidence.get("host_metrics", {})
    if top_level_host_metrics and isinstance(top_level_host_metrics, dict):
        data_quality_issues = top_level_host_metrics.get("data_quality_issues", [])
        if data_quality_issues:
            for issue in data_quality_issues:
                field = issue.get("field", "unknown")
                raw_value = issue.get("raw_value", "unknown")
                explanation = issue.get("explanation", "")
                severity = issue.get("severity", "warning")
                data_quality_warnings.append(
                    f"[{severity.upper()}] {field}: {raw_value} - {explanation[:200]}"
                )

        # Check for interpretation hints
        if "ram_interpretation" in top_level_host_metrics:
            interp = top_level_host_metrics["ram_interpretation"]
            likely_gb = interp.get("likely_value_gb", "unknown")
            raw_val = top_level_host_metrics.get("ram_raw", "unknown")
            data_quality_warnings.append(
                f"[INTERPRETATION] RAM value {raw_val} labeled as percent is likely {likely_gb} GB of memory used"
            )

    # Add data quality warnings to web_run_info if present
    if data_quality_warnings:
        web_run_info += "\n\n⚠️ DATA QUALITY WARNINGS:"
        for warning in data_quality_warnings[:3]:  # Limit to 3 most critical
            web_run_info += f"\n{warning}"

    return {"s3": s3_info, "run": run_info, "batch": batch_info, "web_run": web_run_info}


def _format_investigation_section(investigation: dict) -> str:
    """Format investigation analysis section with data quality awareness."""
    section = "\n## Investigation Analysis\n\n"

    # Evidence sources
    sources_checked = investigation.get("evidence_sources_checked", [])
    sources_skipped = investigation.get("evidence_sources_skipped", [])
    section += f"### Evidence Sources\n- Checked: {', '.join(sources_checked) if sources_checked else 'None'}\n"
    section += f"- Skipped: {', '.join(sources_skipped) if sources_skipped else 'None'}\n"
    section += f"- Tools: {', '.join(investigation.get('tools_executed', []))}\n\n"

    # Check for data quality issues in metrics analysis
    metrics_analysis = investigation.get("metrics_analysis", {})
    if metrics_analysis and isinstance(metrics_analysis, dict):
        anomalies = metrics_analysis.get("anomalies", [])
        data_quality_anomalies = [a for a in anomalies if a.get("type") == "data_quality_error"]
        if data_quality_anomalies:
            section += "### ⚠️ Data Quality Warnings in Metrics\n"
            section += "Some metric values contain unit errors (e.g., bytes labeled as percent). "
            section += "Check for '_interpretation' fields in the evidence to understand the correct units.\n\n"

    # Analysis summaries
    for analysis_type in ["logs_analysis", "tools_analysis", "jobs_analysis", "metrics_analysis"]:
        analysis = investigation.get(analysis_type, {})
        if analysis and analysis.get("summary"):
            section += f"### {analysis_type.replace('_', ' ').title()}\n"
            section += f"- {analysis.get('summary')}\n"

            # Add detailed log information if available
            if analysis_type == "logs_analysis":
                total_logs = analysis.get("total_logs", 0)
                total_errors = analysis.get("total_errors", 0)
                patterns = analysis.get("patterns", [])
                critical_signals = analysis.get("critical_signals", [])

                if total_logs > 0:
                    section += f"- Total logs analyzed: {total_logs} ({total_errors} errors)\n"

                if patterns:
                    section += f"- Error patterns found: {', '.join(patterns[:5])}\n"

                if critical_signals:
                    section += "- Critical log signals:\n"
                    for signal in critical_signals[:5]:
                        level = signal.get("level", "UNKNOWN")
                        message = signal.get("message", "")[:150]
                        section += f"  • [{level}] {message}\n"

            section += "\n"

    # Causal chain
    causal_chain = investigation.get("causal_chain", [])
    if causal_chain:
        section += "### Causal Chain\n"
        for i, event in enumerate(causal_chain[:5], 1):
            event_type = event.get("event_type", "unknown")
            if event_type == "job_failure":
                section += f"{i}. Job {event.get('job_name')} failed: {event.get('status_reason')} (exit_code={event.get('exit_code')})\n"
            elif event_type == "tool_failure":
                section += f"{i}. Tool {event.get('tool_name')} failed: exit_code={event.get('exit_code')}\n"
            elif event_type == "correlation":
                section += f"{i}. Correlation: {event.get('analysis')}\n"
        section += "\n"

    return section


def build_diagnosis_prompt(
    state: InvestigationState, evidence: dict, investigation: dict | None = None
) -> str:
    """Build analysis prompt with deep investigation results."""
    summaries = _format_evidence_summary(evidence)
    investigation_section = _format_investigation_section(investigation) if investigation else ""

    # Check if this is a loop - indicate what evidence is new
    executed_hypotheses = state.get("executed_hypotheses", [])
    loop_count = state.get("investigation_loop_count", 0)
    new_evidence_note = ""
    if loop_count > 0 and executed_hypotheses:
        previous_sources = set()
        for h in executed_hypotheses[:-1]:  # All except the current one
            sources = h.get("sources", [])
            if isinstance(sources, list):
                previous_sources.update(sources)
        if previous_sources:
            new_evidence_note = f"\n\n⚠️ IMPORTANT: This is investigation loop {loop_count + 1}. The following evidence sources were already analyzed in previous loops: {', '.join(sorted(previous_sources))}. Focus on NEW insights from the additional evidence gathered, not re-analyzing what was already seen."

    return f"""You are a root cause analysis expert. Perform DEEP INVESTIGATION, not just correlation.{new_evidence_note}

## Incident
Alert: {state["alert_name"]} | Table: {state["affected_table"]}

## Raw Evidence
### Pipeline: {summaries["run"]}
### Web App Runs: {summaries["web_run"]}
### Batch: {summaries["batch"]}
### S3: {summaries["s3"]}
{investigation_section}
## Your Task: INFER ROOT CAUSE (Not Just Correlate)

You must:
1. **Analyze the causal chain** - What sequence of events led to the failure?
2. **Inspect underlying failure signals** - What do logs, exit codes, and error patterns tell you?
3. **Synthesize multi-source evidence** - How do AWS Batch failures, tool failures, and logs connect?
4. **Identify the root cause** - What is the FUNDAMENTAL reason this happened? (Not just "job failed with exit code X")

### Analysis Requirements:
- Review which evidence sources were checked vs skipped
- Analyze log patterns for failure signals (not just count errors)
- Examine tool execution patterns (failure rates, modes)
- Correlate job failures with tool failures and log signals
- Build a causal chain: what happened first, what cascaded?
- Infer WHY the failure occurred (application error, resource constraint, configuration issue, data problem, etc.)

### Data Quality Awareness:
**CRITICAL: Some evidence may contain data collection errors or impossible values:**
- Check for 'data_quality_issues' fields in the evidence
- Check for fields ending in '_interpretation' which provide unit inference hints
- When you see impossible values (e.g., 8 billion % memory usage), DO NOT treat them as literal
- These are likely unit conversion errors (bytes reported as percent, etc.)
- Use the '_interpretation' hints to understand the most likely correct unit
- State the raw value, explain why it's impossible, then use the interpretation to infer the correct meaning

**Example of handling bad data:**
❌ BAD: "Memory usage was 8,471,740,416% which caused the failure"
✅ GOOD: "Memory metrics show an impossible value of 8,471,740,416 labeled as 'percent' which indicates a unit error. Based on the interpretation hint, this value (8.47 GB if interpreted as bytes) suggests the job used approximately 8.47 GB of memory. Without knowing total memory, exact percentage cannot be determined, but this indicates significant memory usage that may have contributed to the failure."

**How to use interpretation hints:**
- Look for fields like 'ram_interpretation', 'memory.percent_interpretation'
- These contain: likely_unit, likely_value_gb, likely_value_mb, explanation, suggested_fix
- Use the 'likely_value_gb' or 'likely_value_mb' to understand the actual memory usage
- Compare against typical instance memory limits to assess if memory was exhausted

### What NOT to do:
- Do NOT just correlate "job X failed with exit code Y"
- Do NOT assume correlation equals causation
- Do NOT skip analysis of logs and metrics
- Do NOT ignore which tools were executed vs skipped
- Do NOT report impossible values as literal facts (e.g., percentages >100%, "8 billion percent memory")
- Do NOT ignore data_quality_issues warnings in the evidence

### Output Format:
ROOT_CAUSE:
VALIDATED_CLAIMS:
* <claim 1 - directly supported by evidence> [EVIDENCE: <source>]
* <claim 2 - directly supported by evidence> [EVIDENCE: <source>]

NON_VALIDATED_CLAIMS:
* <claim 1 - inferred but not directly supported>
* <claim 2 - inferred but not directly supported>

CAUSAL_CHAIN:
* <event 1 - how events led to failure>
* <event 2 - sequence of events>

CONFIDENCE: <0-100> (based on depth of evidence analysis)"""
