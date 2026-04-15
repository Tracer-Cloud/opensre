"""Evidence formatting and citation for RCA reports."""

from typing import Any

from app.nodes.publish_findings.formatters.base import (
    format_slack_link,
    shorten_text,
)
from app.nodes.publish_findings.report_context import ReportContext
from app.nodes.publish_findings.urls.aws import (
    build_datadog_logs_url,
    build_grafana_explore_url,
)


def _format_tool_calls_line(ctx: ReportContext) -> str:
    """Summarize tool calls made during investigation from executed_hypotheses.

    Returns a compact line like: "Queries: cloudwatch logs (12 events), <Grafana Loki|url> (5 logs)"
    Includes deep links for Grafana and Datadog where endpoints are available.
    Returns empty string if nothing was executed.
    """
    executed_hypotheses = ctx.get("executed_hypotheses", []) or []
    if not executed_hypotheses:
        return ""

    # Collect all action names across all hypothesis rounds (deduped, order preserved)
    all_actions: list[str] = []
    for hyp in executed_hypotheses:
        for action in hyp.get("actions", []):
            if action not in all_actions:
                all_actions.append(action)

    if not all_actions:
        return ""

    evidence = ctx.get("evidence", {}) or {}
    grafana_endpoint = ctx.get("grafana_endpoint") or ""
    datadog_site = ctx.get("datadog_site") or "datadoghq.com"

    def _grafana_logs_count(e: dict) -> str | None:
        logs = e.get("grafana_logs", [])
        errors = e.get("grafana_error_logs", [])
        if not logs and not errors:
            return None
        parts = []
        if logs:
            parts.append(f"{len(logs)} logs")
        if errors:
            parts.append(f"{len(errors)} errors")
        return ", ".join(parts)

    def _datadog_logs_count(e: dict) -> str | None:
        logs = e.get("datadog_logs", [])
        errors = e.get("datadog_error_logs", [])
        if not logs and not errors:
            return None
        parts = []
        if logs:
            parts.append(f"{len(logs)} logs")
        if errors:
            parts.append(f"{len(errors)} errors")
        return ", ".join(parts)

    def _datadog_investigate_count(e: dict) -> str | None:
        logs = e.get("datadog_logs", [])
        errors = e.get("datadog_error_logs", [])
        monitors = e.get("datadog_monitors", [])
        events = e.get("datadog_events", [])
        if not logs and not errors and not monitors and not events:
            return None
        parts = []
        if logs:
            parts.append(f"{len(logs)} logs")
        if errors:
            parts.append(f"{len(errors)} errors")
        if monitors:
            parts.append(f"{len(monitors)} monitors")
        if events:
            parts.append(f"{len(events)} events")
        fetch_ms = e.get("datadog_fetch_ms", {})
        if fetch_ms:
            max_ms = max(v for v in fetch_ms.values() if isinstance(v, int | float))
            if max_ms > 0:
                parts.append(f"fetched in {max_ms / 1000:.1f}s")
        return ", ".join(parts)

    # (label, count_fn, url_fn) — url_fn receives evidence and returns str|None
    ACTION_DEFS: dict[str, tuple[str, Any, Any]] = {
        "get_cloudwatch_logs": (
            "cloudwatch logs",
            lambda e: f"{len(e.get('cloudwatch_logs', []))} events" if e.get("cloudwatch_logs") else None,
            None,
        ),
        "get_error_logs": (
            "error logs",
            lambda e: f"{len(e.get('error_logs', []))} logs" if e.get("error_logs") else None,
            None,
        ),
        "get_failed_jobs": (
            "batch jobs",
            lambda e: f"{len(e.get('failed_jobs', []))} failed" if e.get("failed_jobs") else None,
            None,
        ),
        "get_failed_tools": (
            "tracer tools",
            lambda e: f"{len(e.get('failed_tools', []))} failed" if e.get("failed_tools") else None,
            None,
        ),
        "get_lambda_invocation_logs": (
            "lambda logs",
            lambda e: f"{len(e.get('lambda_logs', []))} logs" if e.get("lambda_logs") else None,
            None,
        ),
        "get_lambda_errors": (
            "lambda errors",
            lambda e: f"{len(e.get('lambda_errors', []))} errors" if e.get("lambda_errors") else None,
            None,
        ),
        "inspect_s3_object": (
            "S3 object",
            lambda e: "found" if (e.get("s3_object") or {}).get("found") else None,
            None,
        ),
        "get_s3_object": (
            "S3 audit payload",
            lambda e: "retrieved" if (e.get("s3_audit_payload") or {}).get("found") else None,
            None,
        ),
        "inspect_lambda_function": (
            "lambda function",
            lambda e: "inspected" if e.get("lambda_function") else None,
            None,
        ),
        "query_grafana_logs": (
            "Grafana Loki",
            _grafana_logs_count,
            lambda e: build_grafana_explore_url(
                grafana_endpoint,
                e.get("grafana_logs_query", ""),
            ) if grafana_endpoint and e.get("grafana_logs_query") else None,
        ),
        "query_grafana_traces": (
            "Grafana Tempo",
            lambda e: f"{len(e.get('grafana_traces', []))} traces" if e.get("grafana_traces") else None,
            lambda _: f"{grafana_endpoint.rstrip('/')}/explore" if grafana_endpoint else None,
        ),
        "query_grafana_metrics": (
            "Grafana Mimir",
            lambda e: f"{len(e.get('grafana_metrics', []))} metrics" if e.get("grafana_metrics") else None,
            lambda _: f"{grafana_endpoint.rstrip('/')}/explore" if grafana_endpoint else None,
        ),
        "query_grafana_alert_rules": (
            "Grafana alerts",
            lambda e: f"{len(e.get('grafana_alert_rules', []))} rules" if e.get("grafana_alert_rules") else None,
            lambda _: f"{grafana_endpoint.rstrip('/')}/alerting/list" if grafana_endpoint else None,
        ),
        "query_datadog_all": (
            "Datadog",
            _datadog_investigate_count,
            lambda e: build_datadog_logs_url(
                e.get("datadog_logs_query", ""),
                datadog_site,
            ) if e.get("datadog_logs_query") else f"https://app.{datadog_site}/logs",
        ),
        "query_datadog_logs": (
            "Datadog Logs",
            _datadog_logs_count,
            lambda e: build_datadog_logs_url(
                e.get("datadog_logs_query", ""),
                datadog_site,
            ) if e.get("datadog_logs_query") else f"https://app.{datadog_site}/logs",
        ),
        "query_datadog_monitors": (
            "Datadog Monitors",
            lambda e: f"{len(e.get('datadog_monitors', []))} monitors" if e.get("datadog_monitors") else None,
            lambda _: f"https://app.{datadog_site}/monitors/manage",
        ),
        "query_datadog_events": (
            "Datadog Events",
            lambda e: f"{len(e.get('datadog_events', []))} events" if e.get("datadog_events") else None,
            lambda _: f"https://app.{datadog_site}/event/explorer",
        ),
    }

    parts: list[str] = []
    for action in all_actions:
        defn = ACTION_DEFS.get(action)
        if defn:
            label, count_fn, url_fn = defn
            count_str = count_fn(evidence)
            url = url_fn(evidence) if url_fn else None
            display = format_slack_link(label, url) if url else label
            if count_str:
                parts.append(f"{display} ({count_str})")
            else:
                parts.append(display)
        else:
            parts.append(action.replace("_", " "))

    return "Queries: " + ", ".join(parts)


def format_cited_evidence_section(ctx: ReportContext) -> str:
    """Format the cited evidence section of the report.

    Shows catalog entries as linked E-id citations, plus a compact summary
    of tool calls made during investigation.

    Returns empty string if there is nothing to show.
    """
    catalog = ctx.get("evidence_catalog") or {}
    lines: list[str] = []

    if catalog:
        def _sort_key(eid: str) -> str:
            return str(catalog[eid].get("display_id", eid))

        for evidence_id in sorted(catalog.keys(), key=_sort_key):
            # Per-pod entries are shown in the Failed Pods section — skip them here
            if evidence_id.startswith("evidence/datadog/failed_pod/"):
                continue
            entry = catalog[evidence_id] or {}
            display_id = entry.get("display_id", evidence_id)
            label = entry.get("label") or evidence_id
            url = entry.get("url")
            summary = entry.get("summary")
            snippet = entry.get("snippet")
            provenance = entry.get("provenance")
            link = format_slack_link(label, url) if url else label
            line = f"- {display_id} — {link}"
            if summary:
                line += f" — {summary}"
            if provenance:
                line += f" — provenance: {provenance}"
            if snippet:
                line += f" — {shorten_text(snippet, max_chars=100)}"
            lines.append(line)

    tool_calls_line = _format_tool_calls_line(ctx)
    if tool_calls_line:
        lines.append(f"- {tool_calls_line}")

    if not lines:
        return ""

    return "\n*Cited Evidence:*\n" + "\n".join(lines) + "\n"
