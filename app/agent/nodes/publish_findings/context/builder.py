"""Extract report context from investigation state."""

import time
from typing import Any

from app.agent.nodes.publish_findings.context.models import ReportContext
from app.agent.nodes.publish_findings.urls.aws import (
    build_datadog_logs_url,
    build_grafana_explore_url,
    build_s3_console_url,
)
from app.agent.state import InvestigationState


def _safe_get(data: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    """Safely navigate nested dictionaries."""
    if data is None:
        return default

    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return default
        current = current.get(key)
        if current is None:
            return default

    return current


def _extract_cloudwatch_info(
    raw_alert: dict[str, Any] | str,
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Extract CloudWatch metadata from alert.

    Returns: (cloudwatch_url, log_group, log_stream, region, alert_id)
    """
    if not isinstance(raw_alert, dict):
        return None, None, None, None, None

    # Try to get annotations from various locations
    annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
    if not annotations and raw_alert.get("alerts"):
        first_alert = raw_alert.get("alerts", [{}])[0]
        if isinstance(first_alert, dict):
            annotations = first_alert.get("annotations", {}) or {}

    # Extract CloudWatch URL
    cloudwatch_url = (
        raw_alert.get("cloudwatch_logs_url")
        or raw_alert.get("cloudwatch_url")
        or _safe_get(annotations, "cloudwatch_logs_url")
        or _safe_get(annotations, "cloudwatch_url")
    )

    # Extract log group and stream
    cloudwatch_group = raw_alert.get("cloudwatch_log_group") or _safe_get(
        annotations, "cloudwatch_log_group"
    )
    cloudwatch_stream = raw_alert.get("cloudwatch_log_stream") or _safe_get(
        annotations, "cloudwatch_log_stream"
    )

    # Extract region
    cloudwatch_region = raw_alert.get("cloudwatch_region") or _safe_get(
        annotations, "cloudwatch_region"
    )

    # Extract alert ID
    alert_id = raw_alert.get("alert_id")

    return cloudwatch_url, cloudwatch_group, cloudwatch_stream, cloudwatch_region, alert_id


def _filter_valid_claims(claims: list[dict]) -> list[dict]:
    """Filter out invalid or junk claims.

    Removes claims that:
    - Have empty claim text
    - Start with "NON_" prefix (artifacts)
    """
    return [
        c
        for c in claims
        if c.get("claim", "").strip() and not c.get("claim", "").strip().startswith("NON_")
    ]


def build_report_context(state: InvestigationState) -> ReportContext:
    """Extract data from state.context and state.evidence for report formatting.

    Args:
        state: Investigation state containing context, evidence, and analysis results

    Returns:
        ReportContext with all data needed for report generation

    Note:
        This function uses defensive access patterns to handle missing or malformed
        data gracefully. Missing fields will use sensible defaults rather than raising
        exceptions.
    """
    # Extract top-level state data
    context = state.get("context", {}) or {}
    evidence = state.get("evidence", {}) or {}
    raw_alert_value = state.get("raw_alert", {})
    raw_alert: dict[str, Any] = raw_alert_value if isinstance(raw_alert_value, dict) else {}

    # Extract nested structures
    web_run = context.get("tracer_web_run", {}) or {}
    batch = evidence.get("batch_jobs", {}) or {}
    s3 = evidence.get("s3", {}) or {}

    # Extract integration endpoints for deep links
    available_sources = state.get("available_sources", {}) or {}
    grafana_endpoint: str | None = (available_sources.get("grafana") or {}).get("grafana_endpoint")
    datadog_site: str | None = (available_sources.get("datadog") or {}).get("site") or "datadoghq.com"

    # Extract and filter claims
    validated_claims = _filter_valid_claims(state.get("validated_claims", []))
    non_validated_claims = state.get("non_validated_claims", [])

    # Extract CloudWatch metadata
    (
        cloudwatch_url,
        cloudwatch_group,
        cloudwatch_stream,
        cloudwatch_region,
        alert_id,
    ) = _extract_cloudwatch_info(raw_alert)

    duration_seconds: int | None = None
    started_at = state.get("investigation_started_at")
    if isinstance(started_at, (int, float)):
        duration_seconds = max(0, int(round(time.monotonic() - float(started_at))))

    # Build evidence catalog (deduplicated artifacts)
    evidence_catalog: dict[str, dict] = {}
    source_to_id: dict[str, str] = {}

    def _as_snippet(value: str | None, max_len: int = 140) -> str | None:
        if not value:
            return None
        compact = " ".join(str(value).split())
        compact = compact.replace("{", "").replace("}", "").replace("[", "").replace("]", "")
        return compact[:max_len]

    def _display_id_for(source_name: str, fallback_index: int) -> str:
        explicit = {
            "s3_metadata": "E1",
            "s3_audit": "E2",
            "cloudwatch_logs": "E3",
            "vendor_audit": "E4",
        }
        return explicit.get(source_name, f"E{fallback_index}")

    s3_obj = evidence.get("s3_object", {}) or {}
    s3_bucket = s3_obj.get("bucket")
    s3_key = s3_obj.get("key")
    if s3_bucket and s3_key:
        s3_url = build_s3_console_url(
            str(s3_bucket),
            str(s3_key),
            cloudwatch_region or "us-east-1",
        )
        eid = "evidence/s3_metadata/landing"
        evidence_catalog[eid] = {
            "label": "S3 Object Metadata",
            "url": s3_url,
            "summary": f"{s3_obj.get('bucket')}/{s3_obj.get('key')}",
            "display_id": _display_id_for("s3_metadata", len(evidence_catalog) + 1),
            "snippet": _as_snippet(
                ", ".join(
                    [
                        f"schema_change_injected={s3_obj.get('metadata', {}).get('schema_change_injected')}",
                        f"schema_version={s3_obj.get('metadata', {}).get('schema_version')}",
                    ]
                ).strip(", ")
            ),
        }
        source_to_id["s3_metadata"] = eid

    s3_audit = evidence.get("s3_audit_payload", {}) or {}
    if s3_audit.get("bucket") and s3_audit.get("key"):
        eid = "evidence/s3_audit/main"
        evidence_catalog[eid] = {
            "label": "S3 Audit Payload",
            "summary": f"{s3_audit.get('bucket')}/{s3_audit.get('key')}",
            "display_id": _display_id_for("s3_audit", len(evidence_catalog) + 1),
            "snippet": _as_snippet(str(s3_audit.get("content", "")) or None),
        }
        source_to_id["s3_audit"] = eid
        source_to_id.setdefault("vendor_audit", eid)

    vendor_audit = evidence.get("vendor_audit_from_logs") or {}
    if vendor_audit and "vendor_audit" not in source_to_id:
        eid = "evidence/vendor_audit/main"
        evidence_catalog[eid] = {
            "label": "Vendor Audit",
            "summary": "External vendor audit record",
            "display_id": _display_id_for("vendor_audit", len(evidence_catalog) + 1),
            "snippet": None,
        }
        source_to_id["vendor_audit"] = eid

    if cloudwatch_url:
        eid = "evidence/cloudwatch/prefect"
        evidence_catalog[eid] = {
            "label": "CloudWatch Logs",
            "url": cloudwatch_url,
            "display_id": _display_id_for("cloudwatch_logs", len(evidence_catalog) + 1),
            "snippet": None,
        }
        source_to_id["cloudwatch_logs"] = eid

    # Grafana Loki logs catalog entry
    grafana_logs = evidence.get("grafana_logs") or []
    grafana_error_logs = evidence.get("grafana_error_logs") or []
    grafana_query = evidence.get("grafana_logs_query") or ""
    grafana_service = evidence.get("grafana_logs_service") or ""
    if grafana_logs or grafana_error_logs:
        grafana_url = build_grafana_explore_url(grafana_endpoint or "", grafana_query) if grafana_query else None
        eid = "evidence/grafana/loki"
        summary_parts = []
        if grafana_service:
            summary_parts.append(grafana_service)
        if grafana_logs:
            summary_parts.append(f"{len(grafana_logs)} logs")
        if grafana_error_logs:
            summary_parts.append(f"{len(grafana_error_logs)} errors")
        evidence_catalog[eid] = {
            "label": "Grafana Loki Logs",
            "url": grafana_url,
            "display_id": f"E{len(evidence_catalog) + 1}",
            "summary": ", ".join(summary_parts) or None,
            "snippet": _as_snippet(grafana_query) if grafana_query else None,
        }
        source_to_id["grafana_logs"] = eid

    # Datadog logs catalog entry
    datadog_logs = evidence.get("datadog_logs") or []
    datadog_error_logs = evidence.get("datadog_error_logs") or []
    datadog_query = evidence.get("datadog_logs_query") or ""
    if datadog_logs or datadog_error_logs:
        dd_url = build_datadog_logs_url(datadog_query, datadog_site or "datadoghq.com") if datadog_query else None
        eid = "evidence/datadog/logs"
        summary_parts = []
        if datadog_logs:
            summary_parts.append(f"{len(datadog_logs)} logs")
        if datadog_error_logs:
            summary_parts.append(f"{len(datadog_error_logs)} errors")
        evidence_catalog[eid] = {
            "label": "Datadog Logs",
            "url": dd_url,
            "display_id": f"E{len(evidence_catalog) + 1}",
            "summary": ", ".join(summary_parts) or None,
            "snippet": _as_snippet(datadog_query) if datadog_query else None,
        }
        source_to_id["datadog_logs"] = eid

    # Attach evidence_ids to claims (validated + non-validated) without mutating originals
    display_map = {eid: entry.get("display_id", eid) for eid, entry in evidence_catalog.items()}

    aliases = {
        "cloudwatch": "cloudwatch_logs",
        "cloudwatch_log": "cloudwatch_logs",
        "cloudwatch_logs": "cloudwatch_logs",
        "grafana": "grafana_logs",
        "grafana_loki": "grafana_logs",
        "datadog": "datadog_logs",
    }

    def _attach_ids(claims: list[dict]) -> list[dict]:
        mapped: list[dict] = []
        for claim in claims:
            new_claim = dict(claim)
            evidence_ids: list[str] = []
            evidence_labels: list[str] = []
            for src in claim.get("evidence_sources", []) or []:
                key = aliases.get(src, src)
                if key == "evidence_analysis":
                    continue
                eid = source_to_id.get(key)
                if eid and eid not in evidence_ids:
                    evidence_ids.append(eid)
                    evidence_labels.append(display_map.get(eid, eid))
            if evidence_ids:
                new_claim["evidence_ids"] = evidence_ids
                new_claim["evidence_labels"] = evidence_labels
            new_claim["evidence_sources"] = []  # normalize display to E-ids only
            mapped.append(new_claim)
        return mapped

    validated_claims = _attach_ids(validated_claims)
    non_validated_claims = _attach_ids(non_validated_claims)

    # Extract causal chain from dependency context (built in build_context node)
    dep_ctx = context.get("dependency_context") or {}
    causal_chain: dict | None = dep_ctx if dep_ctx.get("causal_chain_detected") else None

    # Build context dictionary
    return {
        # Core RCA results
        "pipeline_name": state.get("pipeline_name", "unknown"),
        "root_cause": state.get("root_cause", ""),
        "validated_claims": validated_claims,
        "non_validated_claims": non_validated_claims,
        "validity_score": state.get("validity_score", 0.0),
        "investigation_recommendations": state.get("investigation_recommendations", []),
        "remediation_steps": state.get("remediation_steps", []),
        # S3 verification
        "s3_marker_exists": s3.get("marker_exists", False),
        # Tracer web run metadata
        "tracer_run_status": web_run.get("status"),
        "tracer_run_name": web_run.get("run_name"),
        "tracer_pipeline_name": web_run.get("pipeline_name"),
        "tracer_run_cost": web_run.get("run_cost", 0),
        "tracer_max_ram_gb": web_run.get("max_ram_gb", 0),
        "tracer_user_email": web_run.get("user_email"),
        "tracer_team": web_run.get("team"),
        "tracer_instance_type": web_run.get("instance_type"),
        "tracer_failed_tasks": len(evidence.get("failed_jobs", [])),
        # AWS Batch metadata
        "batch_failure_reason": batch.get("failure_reason"),
        "batch_failed_jobs": batch.get("failed_jobs", 0),
        # CloudWatch metadata
        "cloudwatch_log_group": cloudwatch_group,
        "cloudwatch_log_stream": cloudwatch_stream,
        "cloudwatch_logs_url": cloudwatch_url,
        "cloudwatch_region": cloudwatch_region,
        "alert_id": alert_id,
        "evidence_catalog": evidence_catalog,
        "investigation_duration_seconds": duration_seconds,
        # Raw data for deeper inspection
        "evidence": evidence,
        "raw_alert": raw_alert,
        # Upstream causal chain
        "causal_chain": causal_chain,
        # Tool call history for investigation transparency
        "executed_hypotheses": state.get("executed_hypotheses", []),
        # Integration endpoints for deep links
        "grafana_endpoint": grafana_endpoint,
        "datadog_site": datadog_site,
    }
