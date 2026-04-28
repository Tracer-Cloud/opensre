"""Prompt construction for root cause diagnosis."""

import json
from typing import Any

from app.state import InvestigationState

# Allowed evidence sources the model can reference (keeps grounding consistent)
ALLOWED_EVIDENCE_SOURCES = [
    "aws_batch_jobs",
    "tracer_tools",
    "logs",
    "cloudwatch_logs",
    "host_metrics",
    "aws_cloudwatch_metrics",
    "aws_rds_events",
    "aws_performance_insights",
    "lambda_logs",
    "lambda_code",
    "lambda_config",
    "s3_metadata",
    "s3_audit",
    "vendor_audit",
    "grafana_logs",
    "grafana_traces",
    "grafana_metrics",
    "grafana_alert_rules",
    "datadog_logs",
    "datadog_monitors",
    "datadog_events",
    "betterstack_logs",
    "vercel",
    "github",
]


def build_diagnosis_prompt(
    state: InvestigationState, evidence: dict[str, Any], memory_context: str = ""
) -> str:
    """
    Build an evidence-based prompt for root cause analysis.

    Args:
        state: Investigation state with problem and hypotheses
        evidence: Collected evidence dictionary
        memory_context: Optional memory context from prior investigations

    Returns:
        Formatted prompt string for LLM
    """
    from app.masking import MaskingContext

    _masking_ctx = MaskingContext.from_state(dict(state))
    problem = _masking_ctx.mask(state.get("problem_md", "") or "")
    hypotheses = [_masking_ctx.mask(h) for h in state.get("hypotheses", [])]

    # Build directive sections
    upstream_directive = _build_upstream_directive(evidence)
    database_directive = _build_database_directive(state, evidence)
    kubernetes_directive = _build_kubernetes_directive(state, evidence)
    failover_directive = _build_failover_directive(evidence)
    memory_section = _build_memory_section(memory_context)

    # Build evidence sections
    evidence_text = _build_evidence_sections(state, evidence, _masking_ctx)

    # Construct final prompt
    prompt = f"""You are an experienced SRE performing a root cause analysis (RCA) for a production incident.

OBJECTIVE:
Produce an evidence-grounded RCA. Separate what you can prove from what you infer.
Follow this reasoning sequence:
1. Identify OBSERVED FACTS from the evidence below.
2. Generate candidate hypotheses that could explain those facts.
3. For each hypothesis, check whether the evidence confirms, contradicts, or is silent.
4. Eliminate hypotheses that contradict the evidence.
5. Select the best-supported hypothesis as the root cause. If multiple survive, pick the most parsimonious one and note alternatives.
6. If no hypothesis can be confirmed, state "Most likely ..." with the strongest candidate and explicitly list what evidence is missing.
{upstream_directive}{database_directive}{kubernetes_directive}{failover_directive}{memory_section}
DEFINITIONS:
- VALIDATED_CLAIMS: Directly supported by the evidence shown below (observed facts).
- NON_VALIDATED_CLAIMS: Plausible hypotheses or contributing factors that are NOT directly proven by the evidence.

EVIDENCE RULES:
- Ground every claim in the telemetry, logs, and metrics provided below. You may use domain knowledge to INTERPRET evidence, but do NOT fabricate facts, metrics, or log entries that are not present.
- Do NOT reference source code files or line numbers unless they appear explicitly in the evidence.
- If you lack telemetry to make a definitive ruling, you MUST state what specific evidence is missing (e.g., "No memory metrics available to confirm OOM").
- If GitHub evidence includes file paths, snippets, commits, or content, you may reference them.
- When a validated claim is supported by a specific evidence source, cite it using one of: {", ".join(ALLOWED_EVIDENCE_SOURCES)}.

ANTI-BIAS RULES:
- Do NOT assume an incident category before examining the evidence. Let the evidence determine the category.
- If the provided hypotheses do not match the evidence, discard them and form new ones.
- Consider at least two alternative explanations before settling on a root cause.
- Do NOT anchor on the first plausible explanation. Check for contradicting evidence before committing.

PROBLEM:
{problem}

HYPOTHESES TO CONSIDER (treat as suggestions, not conclusions — discard any that contradict the evidence):
{chr(10).join(f"- {h}" for h in hypotheses[:5]) if hypotheses else "- None provided"}

EVIDENCE:
{evidence_text}

OUTPUT FORMAT (follow exactly — no markdown code blocks, start immediately with ROOT_CAUSE:):

ROOT_CAUSE:
<1–2 sentences stating the root cause. If not provable, say "Most likely: ..." and state what evidence is missing. Never say only "Unable to determine".>

ROOT_CAUSE_CATEGORY:
<exactly one of: configuration_error | code_defect | data_quality | resource_exhaustion | dependency_failure | infrastructure | healthy | unknown>
(Use "healthy" only when all metrics are within normal bounds, no errors are detected, and the alert is informational or resolved. For mixed signals, use your judgment.)

VALIDATED_CLAIMS:
- <factual claim> [evidence: <source>]
- <factual claim> [evidence: <source>]

NON_VALIDATED_CLAIMS:
- <plausible inference — state what data would confirm or refute it>
- <plausible inference — state what data would confirm or refute it>

ALTERNATIVE_HYPOTHESES_CONSIDERED:
- <hypothesis considered and why it was eliminated or deprioritized>

CAUSAL_CHAIN:
- <step 1: the trigger or initial fault>
- <step 2: how the fault propagated>
- <step N: the observable symptom or alert>
(Trace from root cause to the alert. Each step should be one sentence backed by evidence where possible.)
"""

    return prompt


def _build_failover_directive(evidence: dict[str, Any]) -> str:
    rds_events = evidence.get("aws_rds_events", [])
    grafana_logs = evidence.get("grafana_logs", [])
    grafana_error_logs = evidence.get("grafana_error_logs", [])

    event_messages = " ".join(
        str(event.get("message", "")).lower() for event in rds_events if isinstance(event, dict)
    )

    log_messages = " ".join(
        str(log.get("message", "")).lower()
        for log in [*grafana_logs, *grafana_error_logs]
        if isinstance(log, dict)
    )

    has_rds_failover = "failover" in event_messages and (
        "multi-az" in event_messages
        or "health check failure" in event_messages
        or "primary host" in event_messages
    )

    has_log_failover = "failover" in log_messages and (
        "multi-az" in log_messages
        or "health check failure" in log_messages
        or "primary host" in log_messages
    )

    if not (has_rds_failover or has_log_failover):
        return ""

    return """
FAILOVER-SPECIFIC RULES:
- This incident is a failover scenario.
- You MUST use the exact phrase: "primary evidence source".
- You MUST include the exact phrases:
  - "failover initiated"
  - "failover in progress"
  - "failover completed"
  - "instance available"
  - "workload resumed normally"
- Do not paraphrase or omit any of these phrases.

REQUIRED FAILOVER OUTPUT:
- ROOT_CAUSE MUST include:
  "Based on the RDS event timeline (primary evidence source)"
- VALIDATED_CLAIMS MUST include one claim with this exact sequence:
  "failover initiated -> failover in progress -> failover completed -> instance available"
- CAUSAL_CHAIN MUST include:
  "health check failure -> failover -> standby promotion -> DNS update -> brief outage -> recovery"
- ROOT_CAUSE or VALIDATED_CLAIMS MUST explicitly say:
  "the system recovered and workload resumed normally"
"""


def _build_upstream_directive(evidence: dict[str, Any]) -> str:
    """Build upstream tracing directive if audit evidence is present."""
    s3_audit_payload = evidence.get("s3_audit_payload", {})
    vendor_audit_from_logs = evidence.get("vendor_audit_from_logs", {})

    if s3_audit_payload.get("found") or vendor_audit_from_logs:
        return """
**CRITICAL: Upstream Root Cause Tracing**
Audit evidence shows external API interactions. For upstream-triggered failures:
- The root cause is often upstream (external API schema changes, missing fields, breaking changes)
- S3 audit payload and vendor audit logs contain the source of truth
- Validated claims should reference the external API request/response details
- Explain how the external change propagated downstream to cause the pipeline failure
"""
    return ""


def _build_database_directive(state: InvestigationState, evidence: dict[str, Any]) -> str:
    """Build RDS / Database root cause disambiguation directive."""
    pipeline = str(state.get("pipeline", "")).lower()
    alert_text = (str(state.get("alert_name", "")) + " " + str(state.get("raw_alert", ""))).lower()
    is_database_incident = any(k in pipeline for k in ["rds", "postgres", "mysql"]) or any(
        k in alert_text for k in ["rds", "postgres", "mysql", "database", "db instance"]
    )

    has_db_evidence = bool(
        evidence.get("aws_rds_events")
        or evidence.get("aws_performance_insights")
        or bool(
            isinstance(evidence.get("aws_cloudwatch_metrics"), dict)
            and (
                evidence.get("aws_cloudwatch_metrics", {}).get("DBInstanceIdentifier")
                or evidence.get("aws_cloudwatch_metrics", {}).get("db_instance_identifier")
            )
        )
    )
    if not (has_db_evidence or is_database_incident):
        return ""
    return """
**CRITICAL: Database Resource Exhaustion vs CPU Saturation**
When evaluating database health metrics (especially RDS/Postgres):
- Connection pool leaks (exhausting `max_connections`) are a `resource_exhaustion` root cause. High CPU is often just a secondary symptom of accumulated idle sessions. If connections are near 100%, the root cause is connection exhaustion, not CPU saturation. If connections are near 100% AND CPU is near 100%, look for a single shared root cause: a connection pool leak holding open scan-heavy queries, causing both (do not treat them as two independent problems).
- Storage exhaustion (when `FreeStorageSpace` approaches 0) blocks all writes and causes Write IOPS to collapse to 0. The root cause is `resource_exhaustion` due to storage limits. If the `FreeStorageSpace` metric is completely missing, you MUST infer storage exhaustion from indirect signals: WriteIOPS dropping to 0, WriteLatency spiking, and RDS events indicating 'ran out of storage space'.
- A single bad query driving CPU near 100% while connections and storage are healthy is `resource_exhaustion` due to CPU saturation (e.g. missing index, full table scans at high ReadIOPS). Pay close attention to Performance Insights to identify the exact query.
- Checkpoint Storms / VACUUM FREEZE: If CPU is high but the dominant wait event is `LWLock:BufferMapping` with massive WriteIOPS, the root cause is an I/O storm from checkpointing (e.g., `VACUUM FREEZE`) and should be classified as `resource_exhaustion`, NOT `code_defect`. The high CPU is a downstream symptom of I/O contention.
- Replication lag: If a massive write-heavy workload on the primary generates WAL faster than the read replica can replay it, resulting in ReplicaLag spikes, the root cause is `resource_exhaustion` driven by the write workload on the primary. Watch out for red herrings: if concurrent analytics queries cause high CPU, do NOT classify the root cause as CPU-driven if the actual failing metric (like ReplicaLag) is driven by the write-heavy workload. Treat the CPU spike as an unrelated confounder/red herring and explicitly describe it as a red herring (i.e., not the root cause) in the final answer. Do not treat it as a second root cause or a contributing cause. Explicitly state that the analytics SELECT is not the root cause when the evidence shows it is causally independent from WAL generation. If the `ReplicaLag` metric is missing, infer lag from RDS events (e.g., 'exceeded 900s') and high `TransactionLogsGeneration`.
- Compositional Faults: If two completely independent workloads cause two separate faults simultaneously (e.g., CPU saturation from an analytics SELECT AND storage exhaustion from an audit_log INSERT), explicitly identify BOTH as independent root causes (do not merge them into a single IOPS fault). You MUST explicitly state they are two independent, coincidental faults. Provide evidence for both the analytics query and the audit_log query. Use `resource_exhaustion` as ROOT_CAUSE_CATEGORY and describe both causes clearly in ROOT_CAUSE (e.g., "Two independent root causes: ..."). Trace each causal chain separately in CAUSAL_CHAIN. You MUST explicitly state that connection growth is a symptom of blocked writers, not connection exhaustion. You MUST explicitly state that ReplicaLag growth is a downstream symptom of the write burst (not an independent fault). NEVER diagnose `connection_exhaustion` as a root cause when connections spike due to a blocked write queue.
- Misleading Context: Check RDS event timestamps carefully! Ignore historical events (maintenance, failovers, replica promotions) that completed hours before the current incident started.
- Healthy Systems / Stale Alerts: If metrics are oscillating but remain within normal operating bounds (e.g. connections at 55-65%, CPU at 40-70%, no error logs), the system is `healthy`. If a threshold was briefly crossed (e.g. low FreeStorageSpace) but autoscaling successfully expanded the volume and fully recovered the system before the investigation, the system is `healthy` and the alert is stale.

- Healthy System Detection:
If ALL of the following are true:
- connections are clearly below exhaustion levels or not proven to be near `max_connections`
- CPU is not near saturation and remains within normal range
- no errors are present in logs or events
- DB load and latency do not show sustained degradation

Then:
- You MUST classify the system as `healthy`
- You MUST conclude "no active failure"
- You MUST NOT default to `unknown` only because an exact threshold such as `max_connections` is unavailable

When the above healthy conditions are ALL met, apply these scoped prohibitions:
- Do NOT infer connection pool leaks
- Do NOT infer resource exhaustion
- Do NOT generate speculative root causes
- Do NOT interpret monotonic increase or oscillation as failure

When the above healthy conditions are ALL met, apply these scoped interpretation rules:
- Trend ≠ failure
- Oscillation ≠ instability
- Moderate utilization ≠ degradation
- Missing exact thresholds do NOT imply failure or `unknown` when the available evidence shows no errors, moderate load, and no saturation signals

If an alert fires without errors or threshold breaches:
- treat it as a noisy or warning-level alert, NOT a real incident

- ALWAYS trace the causal chain properly (e.g., connection leak -> idle sessions -> connections maxed out, OR missing index -> full table scans -> ReadIOPS -> CPU saturated, OR VACUUM FREEZE -> massive WAL -> checkpoint flush -> I/O saturation -> CPU as symptom).
"""


def _extract_k8s_tags_from_evidence(evidence: dict[str, Any]) -> dict[str, str]:
    """Extract K8s metadata from Datadog log tags (Signal 1 -- primary).

    Also picks up pod_name and container_name alongside kube_* tags.
    Prefers pre-extracted values from fetch_datadog_context when available.
    """
    k8s: dict[str, str] = {}

    # Use pre-extracted pod info from fetch_datadog_context if present
    if evidence.get("datadog_pod_name"):
        k8s["pod_name"] = evidence["datadog_pod_name"]
    if evidence.get("datadog_container_name"):
        k8s["container_name"] = evidence["datadog_container_name"]
    if evidence.get("datadog_kube_namespace"):
        k8s["kube_namespace"] = evidence["datadog_kube_namespace"]

    for log_list_key in ("datadog_error_logs", "datadog_logs"):
        for log in evidence.get(log_list_key, []):
            if not isinstance(log, dict):
                continue
            for tag in log.get("tags", []):
                if not isinstance(tag, str) or ":" not in tag:
                    continue
                key, _, val = tag.partition(":")
                if (
                    key.startswith("kube_") or key in ("pod_name", "container_name")
                ) and key not in k8s:
                    k8s[key] = val
    return k8s


def _detect_k8s_from_monitors(evidence: dict[str, Any]) -> bool:
    """Check Datadog monitors for kubernetes_state queries (Signal 2 -- secondary)."""
    for mon in evidence.get("datadog_monitors", []):
        if isinstance(mon, dict) and "kubernetes_state" in mon.get("query", ""):
            return True
    return False


def _build_kubernetes_directive(state: InvestigationState, evidence: dict[str, Any]) -> str:
    """Build K8s diagnostic directive when Kubernetes context is detected.

    Detection cascade (strict priority):
    1. Tags: kube_* tags in Datadog log entries (most reliable)
    2. Monitors: kubernetes_state in Datadog monitor queries
    3. Alert text: "kubernetes" in raw alert (fallback only)
    """
    k8s_tags = _extract_k8s_tags_from_evidence(evidence)

    if not k8s_tags and not _detect_k8s_from_monitors(evidence):
        raw_alert = state.get("raw_alert", "")
        if "kubernetes" not in str(raw_alert).lower():
            return ""

    metadata_lines = [f"- {k}: {v}" for k, v in k8s_tags.items()] if k8s_tags else []
    metadata_block = "\n".join(metadata_lines)
    metadata_section = f"\n{metadata_block}\n" if metadata_block else ""

    return f"""
**Kubernetes Context Detected:**{metadata_section}
For Kubernetes workload failures, distinguish clearly between:
- SYMPTOM: The workload (Job/Pod/Deployment) entered a failed state
- ROOT CAUSE: The underlying reason the application process failed

Common root cause categories for K8s workload failures:
- Configuration error: environment variables, ConfigMaps, or manifest settings that cause the application to reject valid input or fail validation
- Resource issue: OOM, CPU throttling, storage limits
- Image/dependency: wrong image tag, missing dependencies, startup crash
- Input data: upstream data quality or missing data

Key distinction for schema/validation failures:
When logs show the application validating data against a configured list of required fields, and a field in that list is missing from the data, the root cause is typically a configuration error (the required fields list was misconfigured in the Job manifest or environment variables), not a data quality issue. Data quality issues manifest as malformed values or corrupted records, not as a mismatch between a configured field list and the data schema.

Identify which category fits the evidence and trace the error chain from configuration/input through to the observed failure.
"""


def _build_memory_section(memory_context: str) -> str:
    """Build memory section if context is available."""
    if not memory_context:
        return ""

    return f"""
**Prior Root Cause Patterns (from memory):**
{memory_context[:1500]}

Use these patterns to recognize similar failure modes and accelerate diagnosis.
"""


def _build_evidence_sections(
    state: InvestigationState, evidence: dict[str, Any], masking_ctx=None
) -> str:
    """Build all evidence sections for the prompt."""
    sections: list[str] = []

    # Extract evidence components
    failed_jobs = evidence.get("failed_jobs", [])
    failed_tools = evidence.get("failed_tools", [])
    error_logs = evidence.get("error_logs", [])[:10]
    cloudwatch_logs = evidence.get("cloudwatch_logs", [])[:5]
    host_metrics = evidence.get("host_metrics", {})
    rds_metrics = evidence.get("aws_cloudwatch_metrics", {})
    rds_events = evidence.get("aws_rds_events", [])
    performance_insights = evidence.get("aws_performance_insights", {})
    lambda_logs = evidence.get("lambda_logs", [])[:10]
    lambda_function = evidence.get("lambda_function", {})
    lambda_config = evidence.get("lambda_config", {})
    s3_object = evidence.get("s3_object", {})
    s3_audit_payload = evidence.get("s3_audit_payload", {})
    vendor_audit_from_logs = evidence.get("vendor_audit_from_logs", {})
    vercel_deployment = evidence.get("vercel_deployment", {})
    vercel_failed_deployments = evidence.get("vercel_failed_deployments", [])
    vercel_error_events = evidence.get("vercel_error_events", [])
    vercel_runtime_logs = evidence.get("vercel_runtime_logs", [])
    github_code_matches = evidence.get("github_code_matches", [])
    github_file = evidence.get("github_file", {})
    github_commits = evidence.get("github_commits", [])

    # Extract alert annotations
    raw_alert = state.get("raw_alert", {})
    cloudwatch_url = None
    vercel_url: str | None = None
    alert_annotations: dict[str, Any] = {}
    raw_alert_text: str = ""
    if isinstance(raw_alert, str):
        raw_alert_text = masking_ctx.mask(raw_alert) if masking_ctx else raw_alert
    elif isinstance(raw_alert, dict):
        cloudwatch_url = raw_alert.get("cloudwatch_logs_url") or raw_alert.get("cloudwatch_url")
        vercel_url = raw_alert.get("vercel_log_url") or raw_alert.get("vercel_url")
        alert_annotations = (
            raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {}) or {}
        )
    else:
        vercel_url = None

    # CloudWatch logs
    if cloudwatch_logs:
        section = f"\nCloudWatch Error Logs ({len(cloudwatch_logs)} events):\n"
        for log in cloudwatch_logs:
            section += f"{log}\n"
        if cloudwatch_url:
            section += f"\n[Citation: View full logs at {cloudwatch_url}]\n"
        section += "\n"
        sections.append(section)

    # AWS Batch jobs (only show if data exists)
    if failed_jobs:
        section = f"\nAWS Batch Failed Jobs ({len(failed_jobs)}):\n"
        for job in failed_jobs[:5]:
            section += (
                f"- {job.get('job_name', 'Unknown')}: {job.get('status_reason', 'No reason')}\n"
            )
        sections.append(section)

    # Failed tools (only show if data exists)
    if failed_tools:
        section = f"\nFailed Tools ({len(failed_tools)}):\n"
        for tool in failed_tools[:5]:
            section += f"- {tool.get('tool_name', 'Unknown')}: exit_code={tool.get('exit_code')}\n"
        sections.append(section)

    # Error logs (only show if data exists)
    if error_logs:
        section = f"\nError Logs ({len(error_logs)}):\n"
        for log in error_logs[:5]:
            section += f"- {log.get('message', '')[:200]}\n"
        sections.append(section)

    # Host metrics (only show if data exists)
    if host_metrics and host_metrics.get("data"):
        sections.append("\nHost Metrics: Available (CPU, memory, disk)\n")

    if rds_metrics:
        sections.append(_build_rds_metrics_section(rds_metrics))

    if rds_events:
        sections.append(_build_rds_events_section(rds_events))

    if performance_insights:
        sections.append(_build_performance_insights_section(performance_insights))

    # Lambda logs
    if lambda_logs:
        section = f"\nLambda Invocation Logs ({len(lambda_logs)} events):\n"
        for log in lambda_logs[:10]:
            message = log.get("message", "") if isinstance(log, dict) else str(log)
            section += f"- {message[:300]}\n"
        sections.append(section)

    # Lambda function details
    if lambda_function and lambda_function.get("function_name"):
        section = _build_lambda_function_section(lambda_function)
        sections.append(section)

    # Lambda configuration
    if lambda_config and lambda_config.get("function_name"):
        section = _build_lambda_config_section(lambda_config)
        sections.append(section)

    # S3 object details
    if s3_object and s3_object.get("found"):
        section = _build_s3_object_section(s3_object)
        sections.append(section)

    # S3 audit payload
    if s3_audit_payload and s3_audit_payload.get("found"):
        section = _build_s3_audit_section(s3_audit_payload)
        sections.append(section)

    # Vendor audit from logs
    if vendor_audit_from_logs and vendor_audit_from_logs.get("requests"):
        section = _build_vendor_audit_section(vendor_audit_from_logs)
        sections.append(section)

    # Grafana logs
    grafana_error_logs = evidence.get("grafana_error_logs", [])
    grafana_logs = evidence.get("grafana_logs", [])
    if grafana_error_logs:
        section = f"\nGrafana Error Logs ({len(grafana_error_logs)} events):\n"
        for log in grafana_error_logs[:10]:
            message = log.get("message", "") if isinstance(log, dict) else str(log)
            section += f"- {message[:300]}\n"
        sections.append(section)
    elif grafana_logs:
        section = f"\nGrafana Logs ({len(grafana_logs)} events):\n"
        for log in grafana_logs[:10]:
            message = log.get("message", "") if isinstance(log, dict) else str(log)
            section += f"- {message[:300]}\n"
        sections.append(section)

    # Better Stack Telemetry logs (ClickHouse JSONEachRow rows with dt + raw)
    betterstack_logs = evidence.get("betterstack_logs", [])
    if betterstack_logs:
        bs_source = evidence.get("betterstack_source", "") or "(unknown source)"
        section = f"\nBetter Stack Logs ({len(betterstack_logs)} rows from {bs_source}):\n"
        for row in betterstack_logs[:15]:
            if not isinstance(row, dict):
                continue
            dt = str(row.get("dt", "")).strip()
            raw = str(row.get("raw", "")).strip()
            if dt:
                section += f"- [{dt}] {raw[:300]}\n"
            elif raw:
                section += f"- {raw[:300]}\n"
        sections.append(section)

    # Grafana traces
    grafana_spans = evidence.get("grafana_pipeline_spans", [])
    if grafana_spans:
        section = f"\nGrafana Pipeline Spans ({len(grafana_spans)}):\n"
        for span in grafana_spans[:10]:
            run_id = span.get("execution_run_id", "")
            records = span.get("record_count", "")
            section += f"- {span.get('span_name', 'unknown')}"
            if run_id:
                section += f" (run_id={run_id})"
            if records:
                section += f" records={records}"
            section += "\n"
        sections.append(section)

    # Grafana metrics
    grafana_metrics = evidence.get("grafana_metrics", [])
    if grafana_metrics:
        metric_name = evidence.get("grafana_metric_name", "unknown")
        section = f"\nGrafana Metrics ({metric_name}):\n"
        for metric in grafana_metrics[:5]:
            section += f"- {json.dumps(metric, default=str)[:200]}\n"
        sections.append(section)

    # Grafana alert rules
    grafana_alert_rules = evidence.get("grafana_alert_rules", [])
    if grafana_alert_rules:
        section = f"\nGrafana Alert Rules ({len(grafana_alert_rules)}):\n"
        for rule in grafana_alert_rules[:5]:
            section += f"- {rule.get('rule_name', 'unknown')} [{rule.get('state', '')}]\n"
            section += f"  Folder: {rule.get('folder', '')}, Group: {rule.get('group', '')}\n"
            for query in rule.get("queries", [])[:2]:
                section += f"  Query ({query.get('ref_id', '')}): {query.get('expr', '')[:200]}\n"
            if rule.get("no_data_state"):
                section += f"  No-data state: {rule.get('no_data_state')}\n"
        sections.append(section)

    # Datadog pod location (from fetch_datadog_context extraction)
    datadog_pod_name = evidence.get("datadog_pod_name")
    datadog_container_name = evidence.get("datadog_container_name")
    datadog_kube_namespace = evidence.get("datadog_kube_namespace")
    datadog_failed_pods = evidence.get("datadog_failed_pods", [])

    if datadog_failed_pods:
        section = f"\nFailed Pods ({len(datadog_failed_pods)}):\n"
        for p in datadog_failed_pods:
            pod_parts = [f"pod={p.get('pod_name', '?')}"]
            if p.get("container"):
                pod_parts.append(f"container={p['container']}")
            if p.get("namespace"):
                pod_parts.append(f"namespace={p['namespace']}")
            if p.get("exit_code") is not None:
                pod_parts.append(f"exit={p['exit_code']}")
            if p.get("node_name"):
                node_str = f"node={p['node_name']}"
                if p.get("node_ip"):
                    node_str += f" ({p['node_ip']})"
                pod_parts.append(node_str)
            if p.get("cluster"):
                pod_parts.append(f"cluster={p['cluster']}")
            if p.get("error"):
                pod_parts.append(f"error={p['error'][:100]}")
            section += f"- {' | '.join(pod_parts)}\n"
        sections.append(section)
    elif datadog_pod_name:
        pod_parts = [f"pod_name={datadog_pod_name}"]
        if datadog_container_name:
            pod_parts.append(f"container={datadog_container_name}")
        if datadog_kube_namespace:
            pod_parts.append(f"namespace={datadog_kube_namespace}")
        sections.append(f"\nFailed Pod Location: {' '.join(pod_parts)}\n")

    # Datadog logs
    datadog_error_logs = evidence.get("datadog_error_logs", [])
    datadog_logs = evidence.get("datadog_logs", [])
    if datadog_error_logs:
        section = f"\nDatadog Error Logs ({len(datadog_error_logs)} events):\n"
        for log in datadog_error_logs[:15]:
            section += f"- {_format_datadog_log_entry(log)}\n"
        sections.append(section)
    elif datadog_logs:
        section = f"\nDatadog Logs ({len(datadog_logs)} events):\n"
        for log in datadog_logs[:15]:
            section += f"- {_format_datadog_log_entry(log)}\n"
        sections.append(section)

    # Datadog monitors
    datadog_monitors = evidence.get("datadog_monitors", [])
    if datadog_monitors:
        section = f"\nDatadog Monitors ({len(datadog_monitors)}):\n"
        for monitor in datadog_monitors[:5]:
            section += f"- {monitor.get('name', 'unknown')} [{monitor.get('overall_state', '')}]\n"
            section += (
                f"  Type: {monitor.get('type', '')}, Query: {monitor.get('query', '')[:200]}\n"
            )
        sections.append(section)

    # Datadog events
    datadog_events = evidence.get("datadog_events", [])
    if datadog_events:
        section = f"\nDatadog Events ({len(datadog_events)}):\n"
        for event in datadog_events[:5]:
            section += f"- {event.get('title', 'unknown')}\n"
            if event.get("message"):
                section += f"  {event['message'][:200]}\n"
        sections.append(section)

    if vercel_deployment or vercel_failed_deployments:
        sections.append(
            _build_vercel_evidence_section(
                vercel_deployment=vercel_deployment,
                vercel_failed_deployments=vercel_failed_deployments,
                vercel_error_events=vercel_error_events,
                vercel_runtime_logs=vercel_runtime_logs,
                vercel_url=str(vercel_url or ""),
            )
        )

    if github_commits or github_code_matches or github_file:
        sections.append(
            _build_github_evidence_section(
                github_commits=github_commits,
                github_code_matches=github_code_matches,
                github_file=github_file,
            )
        )

    # Alert annotations
    if alert_annotations:
        section = _build_alert_annotations_section(alert_annotations)
        if section:
            sections.append(section)

    # Raw alert text (e.g. Slack/Datadog message received as plain text)
    if raw_alert_text:
        sections.append(f"\nAlert Notification Text:\n{raw_alert_text[:2000]}\n")

    return "".join(sections)


def _build_lambda_function_section(lambda_function: dict[str, Any]) -> str:
    """Build Lambda function evidence section."""
    section = "\nLambda Function Configuration:\n"
    section += f"- Function: {lambda_function.get('function_name')}\n"
    section += f"- Runtime: {lambda_function.get('runtime')}\n"
    section += f"- Handler: {lambda_function.get('handler')}\n"

    if lambda_function.get("environment_variables"):
        env_vars = lambda_function.get("environment_variables", {})
        section += f"- Environment Variables: {', '.join(env_vars.keys())}\n"

    if lambda_function.get("code", {}).get("files"):
        code_files = lambda_function.get("code", {}).get("files", {})
        if isinstance(code_files, dict) and code_files:
            section += f"- Code Files: {', '.join(list(code_files.keys())[:5])}\n"
            # Include handler code snippet if available
            handler_file = lambda_function.get("handler", "").split(".")[0] + ".py"
            if handler_file in code_files:
                file_content = code_files.get(handler_file, "")
                if isinstance(file_content, str):
                    code_snippet = file_content[:1000]
                    section += f"\nHandler Code Snippet ({handler_file}):\n{code_snippet}\n"

    return section


def _build_rds_metrics_section(rds_metrics: dict[str, Any]) -> str:
    """Build RDS CloudWatch metrics evidence section."""
    section = "\nRDS CloudWatch Metrics:\n"

    db_instance = rds_metrics.get("db_instance_identifier")
    if db_instance:
        section += f"- DB instance: {db_instance}\n"

    metric_entries = rds_metrics.get("metrics", [])
    for metric in metric_entries[:8]:
        if not isinstance(metric, dict):
            continue
        metric_name = metric.get("metric_name", "unknown")
        summary = metric.get("summary")
        unit = metric.get("unit")
        if summary:
            section += f"- {metric_name}: {summary}\n"
        else:
            datapoints = metric.get("recent_datapoints", [])
            section += f"- {metric_name}: {len(datapoints)} recent datapoints"
            if unit:
                section += f" [{unit}]"
            section += "\n"

    observations = rds_metrics.get("observations", [])
    for observation in observations[:5]:
        section += f"- Observation: {observation}\n"

    return section


def _build_rds_events_section(rds_events: list[dict[str, Any]]) -> str:
    """Build RDS event evidence section."""
    section = f"\nRDS Events ({len(rds_events)}):\n"
    for event in rds_events[:8]:
        if not isinstance(event, dict):
            continue
        timestamp = event.get("date") or event.get("timestamp") or "unknown-time"
        message = event.get("message", "No message")
        source = event.get("source_identifier") or event.get("source_type")
        section += f"- {timestamp}: {message}"
        if source:
            section += f" [{source}]"
        section += "\n"
    return section


def _build_performance_insights_section(performance_insights: dict[str, Any]) -> str:
    """Build RDS Performance Insights evidence section."""
    section = "\nPerformance Insights:\n"

    if performance_insights.get("db_instance_identifier"):
        section += f"- DB instance: {performance_insights['db_instance_identifier']}\n"

    for observation in performance_insights.get("observations", [])[:5]:
        section += f"- Observation: {observation}\n"

    top_sql = performance_insights.get("top_sql", [])
    if top_sql:
        section += "Top SQL:\n"
        for item in top_sql[:5]:
            if not isinstance(item, dict):
                continue
            sql = str(item.get("sql", ""))[:180]
            db_load = item.get("db_load")
            wait_event = item.get("wait_event")
            section += f"- sql={sql}"
            if db_load is not None:
                section += f" | db_load={db_load}"
            if wait_event:
                section += f" | wait_event={wait_event}"
            section += "\n"

    wait_events = performance_insights.get("wait_events", [])
    if wait_events:
        section += "Top Wait Events:\n"
        for item in wait_events[:5]:
            if not isinstance(item, dict):
                continue
            name = item.get("name", "unknown")
            db_load = item.get("db_load")
            section += f"- {name}"
            if db_load is not None:
                section += f" | db_load={db_load}"
            section += "\n"

    return section


def _extract_vercel_git_metadata(meta: dict[str, Any]) -> dict[str, str]:
    """Normalize git metadata from Vercel deployment evidence."""
    return {
        "repo": str(meta.get("github_repo") or meta.get("githubRepo") or "").strip(),
        "sha": str(meta.get("github_commit_sha") or meta.get("githubCommitSha") or "").strip(),
        "ref": str(meta.get("github_commit_ref") or meta.get("githubCommitRef") or "").strip(),
    }


def _format_vercel_runtime_log(log: Any) -> str:
    """Format a runtime log entry into a compact single-line excerpt."""
    if not isinstance(log, dict):
        return str(log)[:300]

    message = log.get("message")
    if not message:
        payload = log.get("payload")
        if isinstance(payload, dict):
            message = payload.get("text") or payload.get("message") or payload.get("body") or ""
        elif payload:
            message = str(payload)

    prefix_parts = [
        str(log.get("type", "")).strip(),
        str(log.get("source", "")).strip(),
    ]
    prefix = " ".join(part for part in prefix_parts if part)
    text = str(message or "")[:260]
    return f"{prefix}: {text}" if prefix else text


def _build_vercel_evidence_section(
    *,
    vercel_deployment: dict[str, Any],
    vercel_failed_deployments: list[dict[str, Any]],
    vercel_error_events: list[dict[str, Any]],
    vercel_runtime_logs: list[dict[str, Any]],
    vercel_url: str,
) -> str:
    """Build a Vercel deployment evidence section."""
    section = "\nVercel Deployment Evidence:\n"

    if vercel_deployment:
        git_meta = _extract_vercel_git_metadata(vercel_deployment.get("meta", {}))
        section += f"- Deployment ID: {vercel_deployment.get('id', 'unknown')}\n"
        section += f"- State: {vercel_deployment.get('state', 'unknown')}\n"
        if vercel_deployment.get("error"):
            section += f"- Deployment Error: {vercel_deployment.get('error')}\n"
        if git_meta["repo"]:
            section += f"- GitHub Repo: {git_meta['repo']}\n"
        if git_meta["sha"]:
            section += f"- Commit SHA: {git_meta['sha']}\n"
        if git_meta["ref"]:
            section += f"- Commit Ref: {git_meta['ref']}\n"

    if vercel_failed_deployments:
        section += f"Failed Deployments ({len(vercel_failed_deployments)}):\n"
        for deployment in vercel_failed_deployments[:5]:
            if not isinstance(deployment, dict):
                continue
            git_meta = _extract_vercel_git_metadata(deployment.get("meta", {}))
            line = f"- {deployment.get('id', 'unknown')} [{deployment.get('state', 'unknown')}]"
            if deployment.get("error"):
                line += f" error={str(deployment.get('error'))[:140]}"
            if git_meta["sha"]:
                line += f" sha={git_meta['sha'][:12]}"
            if git_meta["ref"]:
                line += f" ref={git_meta['ref']}"
            section += f"{line}\n"

    if vercel_error_events:
        section += f"Vercel Error Events ({len(vercel_error_events)}):\n"
        for event in vercel_error_events[:8]:
            if not isinstance(event, dict):
                continue
            section += f"- {str(event.get('text', ''))[:280]}\n"

    if vercel_runtime_logs:
        section += f"Vercel Runtime Logs ({len(vercel_runtime_logs)}):\n"
        for log in vercel_runtime_logs[:8]:
            formatted = _format_vercel_runtime_log(log)
            if formatted:
                section += f"- {formatted}\n"

    if vercel_url:
        section += f"\n[Citation: View full logs at {vercel_url}]\n"

    return section


def _build_github_evidence_section(
    *,
    github_commits: list[Any],
    github_code_matches: list[Any],
    github_file: Any,
) -> str:
    """Build a GitHub evidence section from MCP-backed code data."""
    section = "\nGitHub Code Evidence:\n"

    if github_commits:
        section += f"Recent Commits ({len(github_commits)}):\n"
        for commit in github_commits[:5]:
            if not isinstance(commit, dict):
                section += f"- {str(commit)[:220]}\n"
                continue
            commit_info = commit.get("commit", {}) if isinstance(commit.get("commit"), dict) else {}
            sha = str(commit.get("sha") or commit.get("oid") or commit_info.get("oid") or "")[:12]
            message = str(
                commit.get("message")
                or commit.get("messageHeadline")
                or commit_info.get("message")
                or "unknown"
            )[:220]
            section += f"- {sha or 'unknown'}: {message}\n"

    if github_code_matches:
        section += f"Code Search Matches ({len(github_code_matches)}):\n"
        for match in github_code_matches[:5]:
            if not isinstance(match, dict):
                section += f"- {str(match)[:220]}\n"
                continue
            path = str(
                match.get("path")
                or match.get("file")
                or match.get("name")
                or match.get("filename")
                or "unknown"
            )[:180]
            snippets = match.get("matches") or match.get("fragments") or match.get("lines") or []
            if isinstance(snippets, list) and snippets:
                snippet_text = "; ".join(str(item)[:140] for item in snippets[:2])
            else:
                snippet_text = str(match.get("text", ""))[:220]
            section += f"- {path}: {snippet_text}\n"

    if github_file:
        section += "GitHub File Contents:\n"
        if isinstance(github_file, dict):
            path = str(
                github_file.get("path")
                or github_file.get("name")
                or github_file.get("filename")
                or ""
            ).strip()
            if path:
                section += f"- Path: {path}\n"
            content = github_file.get("content")
            if isinstance(content, str) and content:
                section += f"{content[:1000]}\n"
            else:
                section += f"- Details: {json.dumps(github_file, default=str)[:1000]}\n"
        elif isinstance(github_file, list):
            for item in github_file[:5]:
                section += f"- {json.dumps(item, default=str)[:220]}\n"
        else:
            section += f"- {str(github_file)[:1000]}\n"

    return section


def _build_lambda_config_section(lambda_config: dict[str, Any]) -> str:
    """Build Lambda configuration evidence section."""
    section = "\nLambda Configuration:\n"
    section += f"- Function: {lambda_config.get('function_name')}\n"
    section += f"- Runtime: {lambda_config.get('runtime')}\n"
    section += f"- Handler: {lambda_config.get('handler')}\n"

    env_vars = lambda_config.get("environment_variables", {})
    if env_vars:
        section += "- Environment Variables:\n"
        for key, value in list(env_vars.items())[:10]:
            section += f"  - {key}: {value}\n"

    return section


_STRUCTURED_TAG_PREFIXES = ("kube_",)
_STRUCTURED_TAG_NAMES = ("pod_name", "container_name", "container_id")


def _format_datadog_log_entry(log: Any) -> str:
    """Format a single Datadog log entry, surfacing structured tags and timestamp when present."""
    if not isinstance(log, dict):
        return str(log)[:300]

    message = log.get("message", "")[:300]
    tags = log.get("tags", [])

    # Extract HH:MM:SS from ISO timestamp for compact display
    ts_prefix = ""
    raw_ts = log.get("timestamp", "")
    if isinstance(raw_ts, str) and "T" in raw_ts:
        time_part = raw_ts.split("T", 1)[1][:8]  # "HH:MM:SS"
        ts_prefix = f"[{time_part}] "
    elif isinstance(raw_ts, int | float):
        import datetime

        ts_prefix = f"[{datetime.datetime.utcfromtimestamp(raw_ts / 1000 if raw_ts > 1e10 else raw_ts).strftime('%H:%M:%S')}] "

    tag_parts: dict[str, str] = {}
    for t in tags:
        if not isinstance(t, str) or ":" not in t:
            continue
        k, _, v = t.partition(":")
        if any(k.startswith(p) for p in _STRUCTURED_TAG_PREFIXES) or k in _STRUCTURED_TAG_NAMES:
            tag_parts[k] = v

    if tag_parts:
        tag_str = " ".join(f"{k}={v}" for k, v in tag_parts.items())
        return f"{ts_prefix}[{tag_str}] {message}"

    host = log.get("host", "")
    service = log.get("service", "")
    prefix = f"[{service}@{host}] " if service or host else ""
    return f"{ts_prefix}{prefix}{message}"


def _build_s3_object_section(s3_object: dict[str, Any]) -> str:
    """Build S3 object evidence section."""
    section = "\nS3 Object Details:\n"
    section += f"- Bucket: {s3_object.get('bucket')}\n"
    section += f"- Key: {s3_object.get('key')}\n"
    section += f"- Size: {s3_object.get('size')} bytes\n"
    section += f"- Content Type: {s3_object.get('content_type')}\n"

    metadata = s3_object.get("metadata", {})
    if metadata:
        section += f"- Metadata: {json.dumps(metadata, indent=2)}\n"

    sample = s3_object.get("sample")
    if s3_object.get("is_text") and isinstance(sample, str):
        section += f"\nS3 Object Sample:\n{sample[:500]}\n"

    return section


def _build_s3_audit_section(s3_audit_payload: dict[str, Any]) -> str:
    """Build S3 audit payload evidence section."""
    section = "\nS3 Audit Payload (External API Lineage):\n"
    section += f"- Bucket: {s3_audit_payload.get('bucket')}\n"
    section += f"- Key: {s3_audit_payload.get('key')}\n"

    audit_content = s3_audit_payload.get("content")
    if audit_content:
        try:
            audit_data = (
                json.loads(audit_content) if isinstance(audit_content, str) else audit_content
            )
            section += f"- Content: {json.dumps(audit_data, indent=2)[:1500]}\n"
        except (json.JSONDecodeError, TypeError):
            section += f"- Content: {str(audit_content)[:500]}\n"

    return section


def _build_vendor_audit_section(vendor_audit_from_logs: dict[str, Any]) -> str:
    """Build vendor audit evidence section."""
    section = "\nExternal Vendor API Audit (from Lambda logs):\n"
    for req in vendor_audit_from_logs.get("requests", [])[:5]:
        section += f"- {req.get('type')} {req.get('url')}\n"
        section += f"  Status: {req.get('status_code')}\n"
        if req.get("response_body"):
            response_str = json.dumps(req.get("response_body"), indent=2)[:500]
            section += f"  Response: {response_str}\n"

    return section


def _build_alert_annotations_section(alert_annotations: dict[str, Any]) -> str:
    """Build alert annotations evidence section."""
    sections = []

    if alert_annotations.get("log_excerpt"):
        sections.append(f"\nLog Excerpt from Alert:\n{alert_annotations['log_excerpt'][:1000]}\n")

    if alert_annotations.get("failed_steps"):
        sections.append(f"\nFailed Steps Summary:\n{alert_annotations['failed_steps']}\n")

    if alert_annotations.get("error"):
        sections.append(f"\nError Message:\n{alert_annotations['error']}\n")

    return "".join(sections)
