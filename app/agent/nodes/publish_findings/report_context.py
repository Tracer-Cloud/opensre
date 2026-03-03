"""Extract report context from investigation state.

ReportContext is a plain TypedDict (no logic) that carries everything
the formatters need to render the final RCA report.

build_report_context runs four phases:
1. _NormalizedState     – pull raw dicts out of state, coerce types
2. _build_evidence_catalog – populate evidence entries per source
3. _attach_evidence_to_claims – link claims to catalog entries by source key
4. build_report_context – assemble the final ReportContext dict
"""

from __future__ import annotations

import time
from typing import Any, TypedDict

from app.agent.nodes.publish_findings.urls.aws import (
    build_datadog_logs_url,
    build_grafana_explore_url,
    build_s3_console_url,
)
from app.agent.state import InvestigationState

# ---------------------------------------------------------------------------
# ReportContext — the schema that all formatters read from
# ---------------------------------------------------------------------------

class ReportContext(TypedDict, total=False):
    """Data extracted from state for report formatting.

    Contains all information needed to generate the final RCA report,
    including pipeline metadata, root cause analysis results, validated claims,
    infrastructure assets, and evidence references.
    """

    # Core RCA results
    pipeline_name: str
    alert_name: str | None
    root_cause: str
    root_cause_category: str | None
    validated_claims: list[dict]
    non_validated_claims: list[dict]
    validity_score: float
    investigation_recommendations: list[str]
    remediation_steps: list[str]

    # S3 verification
    s3_marker_exists: bool

    # Tracer web run metadata
    tracer_run_status: str | None
    tracer_run_name: str | None
    tracer_pipeline_name: str | None
    tracer_run_cost: float
    tracer_max_ram_gb: float
    tracer_user_email: str | None
    tracer_team: str | None
    tracer_instance_type: str | None
    tracer_failed_tasks: int

    # AWS Batch metadata
    batch_failure_reason: str | None
    batch_failed_jobs: int

    # CloudWatch metadata
    cloudwatch_log_group: str | None
    cloudwatch_log_stream: str | None
    cloudwatch_logs_url: str | None
    cloudwatch_region: str | None
    alert_id: str | None
    evidence_catalog: dict
    investigation_duration_seconds: int | None

    # Raw data for deeper inspection
    evidence: dict  # Raw evidence data for citation
    raw_alert: dict  # Raw alert for infrastructure extraction

    # Tool call history for investigation transparency
    executed_hypotheses: list[dict]

    # Integration endpoints (for building deep links)
    grafana_endpoint: str | None
    datadog_site: str | None

    kube_pod_name: str | None
    kube_container_name: str | None
    kube_namespace: str | None

    # Multiple failed pods (for cluster-scale failures)
    kube_failed_pods: list[dict]  # [{pod_name, container, namespace, exit_code, error}]


# ---------------------------------------------------------------------------
# Source name aliases used when matching claim.evidence_sources → catalog IDs
# ---------------------------------------------------------------------------

_SOURCE_ALIASES: dict[str, str] = {
    "cloudwatch": "cloudwatch_logs",
    "cloudwatch_log": "cloudwatch_logs",
    "cloudwatch_logs": "cloudwatch_logs",
    "grafana": "grafana_logs",
    "grafana_loki": "grafana_logs",
    "datadog": "datadog_logs",
}


# ---------------------------------------------------------------------------
# Small utilities
# ---------------------------------------------------------------------------

def _safe_get(data: dict[str, Any] | None, *keys: str, default: Any = None) -> Any:
    """Safely navigate nested dictionaries without raising."""
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


def _as_snippet(value: str | None, max_len: int = 140) -> str | None:
    """Compact a value to a short, brace-free snippet for display."""
    if not value:
        return None
    compact = " ".join(str(value).split())
    compact = compact.replace("{", "").replace("}", "").replace("[", "").replace("]", "")
    return compact[:max_len]


def _filter_valid_claims(claims: list[dict]) -> list[dict]:
    """Drop claims with empty text or the NON_ artifact prefix."""
    return [
        c
        for c in claims
        if c.get("claim", "").strip() and not c.get("claim", "").strip().startswith("NON_")
    ]


# ---------------------------------------------------------------------------
# Phase 1: state normalization
# ---------------------------------------------------------------------------

class _NormalizedState:
    """All raw data extracted from InvestigationState in one place."""

    def __init__(self, state: InvestigationState) -> None:
        context = state.get("context", {}) or {}
        evidence = state.get("evidence", {}) or {}
        raw_alert_value = state.get("raw_alert", {})

        self.evidence: dict[str, Any] = evidence
        self.raw_alert: dict[str, Any] = raw_alert_value if isinstance(raw_alert_value, dict) else {}
        self.web_run: dict[str, Any] = context.get("tracer_web_run", {}) or {}
        self.batch: dict[str, Any] = evidence.get("batch_jobs", {}) or {}
        self.s3: dict[str, Any] = evidence.get("s3", {}) or {}

        available_sources = state.get("available_sources", {}) or {}
        self.grafana_endpoint: str | None = (available_sources.get("grafana") or {}).get("grafana_endpoint")
        self.datadog_site: str = (available_sources.get("datadog") or {}).get("site") or "datadoghq.com"

        self.validated_claims: list[dict] = _filter_valid_claims(state.get("validated_claims", []))
        self.non_validated_claims: list[dict] = state.get("non_validated_claims", [])

        self.cloudwatch_url, self.cloudwatch_group, self.cloudwatch_stream, \
            self.cloudwatch_region, self.alert_id = _extract_cloudwatch_info(self.raw_alert)

        started_at = state.get("investigation_started_at")
        self.duration_seconds: int | None = (
            max(0, int(round(time.monotonic() - float(started_at))))
            if isinstance(started_at, (int, float))
            else None
        )

        self.state = state


def _extract_cloudwatch_info(
    raw_alert: dict[str, Any],
) -> tuple[str | None, str | None, str | None, str | None, str | None]:
    """Pull CloudWatch metadata from an alert dict.

    Returns: (url, log_group, log_stream, region, alert_id)
    """
    annotations = raw_alert.get("annotations", {}) or raw_alert.get("commonAnnotations", {})
    if not annotations and raw_alert.get("alerts"):
        first_alert = raw_alert.get("alerts", [{}])[0]
        if isinstance(first_alert, dict):
            annotations = first_alert.get("annotations", {}) or {}

    url = (
        raw_alert.get("cloudwatch_logs_url")
        or raw_alert.get("cloudwatch_url")
        or _safe_get(annotations, "cloudwatch_logs_url")
        or _safe_get(annotations, "cloudwatch_url")
    )
    group = raw_alert.get("cloudwatch_log_group") or _safe_get(annotations, "cloudwatch_log_group")
    stream = raw_alert.get("cloudwatch_log_stream") or _safe_get(annotations, "cloudwatch_log_stream")
    region = raw_alert.get("cloudwatch_region") or _safe_get(annotations, "cloudwatch_region")
    alert_id = raw_alert.get("alert_id")
    return url, group, stream, region, alert_id


# ---------------------------------------------------------------------------
# Phase 2: evidence catalog construction
#
# Each _add_* helper appends to the shared (catalog, source_to_id) accumulators.
# ---------------------------------------------------------------------------

def _add_s3_metadata(
    evidence: dict[str, Any],
    region: str | None,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    s3_obj = evidence.get("s3_object", {}) or {}
    bucket, key = s3_obj.get("bucket"), s3_obj.get("key")
    if not (bucket and key):
        return
    eid = "evidence/s3_metadata/landing"
    meta = s3_obj.get("metadata", {}) or {}
    catalog[eid] = {
        "label": "S3 Object Metadata",
        "url": build_s3_console_url(str(bucket), str(key), region or "us-east-1"),
        "summary": f"{bucket}/{key}",
        "snippet": _as_snippet(
            f"schema_change_injected={meta.get('schema_change_injected')}, "
            f"schema_version={meta.get('schema_version')}"
        ),
    }
    source_to_id["s3_metadata"] = eid


def _add_s3_audit(
    evidence: dict[str, Any],
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    s3_audit = evidence.get("s3_audit_payload", {}) or {}
    if not (s3_audit.get("bucket") and s3_audit.get("key")):
        return
    eid = "evidence/s3_audit/main"
    catalog[eid] = {
        "label": "S3 Audit Payload",
        "summary": f"{s3_audit['bucket']}/{s3_audit['key']}",
        "snippet": _as_snippet(str(s3_audit.get("content", "")) or None),
    }
    source_to_id["s3_audit"] = eid
    source_to_id.setdefault("vendor_audit", eid)


def _add_vendor_audit(
    evidence: dict[str, Any],
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    vendor_audit = evidence.get("vendor_audit_from_logs") or {}
    if not vendor_audit or "vendor_audit" in source_to_id:
        return
    eid = "evidence/vendor_audit/main"
    catalog[eid] = {
        "label": "Vendor Audit",
        "summary": "External vendor audit record",
        "snippet": None,
    }
    source_to_id["vendor_audit"] = eid


def _add_cloudwatch(
    cloudwatch_url: str | None,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    if not cloudwatch_url:
        return
    eid = "evidence/cloudwatch/prefect"
    catalog[eid] = {
        "label": "CloudWatch Logs",
        "url": cloudwatch_url,
        "snippet": None,
    }
    source_to_id["cloudwatch_logs"] = eid


def _add_grafana_logs(
    evidence: dict[str, Any],
    grafana_endpoint: str | None,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    grafana_logs = evidence.get("grafana_logs") or []
    grafana_error_logs = evidence.get("grafana_error_logs") or []
    if not (grafana_logs or grafana_error_logs):
        return
    grafana_query = evidence.get("grafana_logs_query") or ""
    grafana_service = evidence.get("grafana_logs_service") or ""
    summary_parts = [p for p in [
        grafana_service or None,
        f"{len(grafana_logs)} logs" if grafana_logs else None,
        f"{len(grafana_error_logs)} errors" if grafana_error_logs else None,
    ] if p]
    eid = "evidence/grafana/loki"
    catalog[eid] = {
        "label": "Grafana Loki Logs",
        "url": build_grafana_explore_url(grafana_endpoint or "", grafana_query) if grafana_query else None,
        "summary": ", ".join(summary_parts) or None,
        "snippet": _as_snippet(grafana_query) if grafana_query else None,
    }
    source_to_id["grafana_logs"] = eid


def _add_datadog_logs(
    evidence: dict[str, Any],
    datadog_site: str,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    datadog_logs = evidence.get("datadog_logs") or []
    datadog_error_logs = evidence.get("datadog_error_logs") or []
    if not (datadog_logs or datadog_error_logs):
        return
    datadog_query = evidence.get("datadog_logs_query") or ""
    summary_parts = [p for p in [
        f"{len(datadog_logs)} logs" if datadog_logs else None,
        f"{len(datadog_error_logs)} errors" if datadog_error_logs else None,
    ] if p]
    top_msg = next(
        (e.get("message", "").strip() for e in (datadog_error_logs or datadog_logs) if e.get("message")),
        None,
    )
    eid = "evidence/datadog/logs"
    catalog[eid] = {
        "label": "Datadog Logs",
        "url": build_datadog_logs_url(datadog_query, datadog_site) if datadog_query else None,
        "summary": ", ".join(summary_parts) or None,
        "snippet": _as_snippet(top_msg) if top_msg else (_as_snippet(datadog_query) if datadog_query else None),
    }
    source_to_id["datadog_logs"] = eid


def _add_datadog_monitors(
    evidence: dict[str, Any],
    datadog_site: str,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    datadog_monitors = evidence.get("datadog_monitors") or []
    if not datadog_monitors:
        return
    triggered = [m for m in datadog_monitors if m.get("overall_state") in ("Alert", "Warn", "No Data")]
    label = (
        f"Datadog Monitors ({len(triggered)} triggered)"
        if triggered
        else f"Datadog Monitors ({len(datadog_monitors)})"
    )
    eid = "evidence/datadog/monitors"
    catalog[eid] = {
        "label": label,
        "url": f"https://app.{datadog_site}/monitors/manage",
        "summary": f"{len(datadog_monitors)} monitors",
        "snippet": _as_snippet(", ".join(m.get("name", "") for m in datadog_monitors[:3])),
    }
    source_to_id["datadog_monitors"] = eid


def _add_datadog_events(
    evidence: dict[str, Any],
    datadog_site: str,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    datadog_events = evidence.get("datadog_events") or []
    if not datadog_events:
        return
    eid = "evidence/datadog/events"
    catalog[eid] = {
        "label": f"Datadog Events ({len(datadog_events)})",
        "url": f"https://app.{datadog_site}/event/explorer",
        "summary": f"{len(datadog_events)} events",
        "snippet": _as_snippet(datadog_events[0].get("title", "")),
    }
    source_to_id["datadog_events"] = eid


def _add_datadog_failed_pods(
    evidence: dict[str, Any],
    datadog_site: str,
    catalog: dict[str, dict],
    source_to_id: dict[str, str],
) -> None:
    dd_ns = evidence.get("datadog_kube_namespace")
    dd_container = evidence.get("datadog_container_name")
    raw_pods: list[dict] = evidence.get("datadog_failed_pods", [])
    if not raw_pods and evidence.get("datadog_pod_name"):
        raw_pods = [{"pod_name": evidence["datadog_pod_name"], "namespace": dd_ns, "container": dd_container}]

    for idx, pod in enumerate(raw_pods):
        pname = pod.get("pod_name") or pod.get("name")
        if not pname:
            continue
        pns = pod.get("namespace") or pod.get("kube_namespace") or dd_ns
        pcontainer = pod.get("container") or pod.get("container_name") or dd_container
        pod_query = f"kube_namespace:{pns} pod_name:{pname}" if pns else f"pod_name:{pname}"
        summary_parts = [f"namespace={pns}"] if pns else []
        if pod.get("exit_code") is not None:
            summary_parts.append(f"exit={pod['exit_code']}")
        if pod.get("memory_requested") and pod.get("memory_limit"):
            summary_parts.append(f"mem requested={pod['memory_requested']} limit={pod['memory_limit']}")
        eid = f"evidence/datadog/failed_pod/{pname}"
        catalog[eid] = {
            "label": f"Failed Pod: {pname}{f' ({pcontainer})' if pcontainer else ''}",
            "url": build_datadog_logs_url(pod_query, datadog_site),
            "summary": ", ".join(summary_parts) if summary_parts else pname,
            "snippet": pod.get("error"),
        }
        if idx == 0:
            source_to_id["datadog_pod"] = eid


def _build_evidence_catalog(
    ns: _NormalizedState,
) -> tuple[dict[str, dict], dict[str, str]]:
    """Build the full evidence catalog and the source-name → catalog-id index.

    Display IDs (E1, E2, …) are assigned in a single sequential pass after all
    entries are added, so they always reflect actual insertion order with no gaps.
    """
    catalog: dict[str, dict] = {}
    source_to_id: dict[str, str] = {}

    _add_s3_metadata(ns.evidence, ns.cloudwatch_region, catalog, source_to_id)
    _add_s3_audit(ns.evidence, catalog, source_to_id)
    _add_vendor_audit(ns.evidence, catalog, source_to_id)
    _add_cloudwatch(ns.cloudwatch_url, catalog, source_to_id)
    _add_grafana_logs(ns.evidence, ns.grafana_endpoint, catalog, source_to_id)
    _add_datadog_logs(ns.evidence, ns.datadog_site, catalog, source_to_id)
    _add_datadog_monitors(ns.evidence, ns.datadog_site, catalog, source_to_id)
    _add_datadog_events(ns.evidence, ns.datadog_site, catalog, source_to_id)
    _add_datadog_failed_pods(ns.evidence, ns.datadog_site, catalog, source_to_id)

    for i, entry in enumerate(catalog.values()):
        entry["display_id"] = f"E{i + 1}"

    return catalog, source_to_id


# ---------------------------------------------------------------------------
# Phase 3: link claims to catalog entries
# ---------------------------------------------------------------------------

def _attach_evidence_to_claims(
    claims: list[dict],
    source_to_id: dict[str, str],
    display_map: dict[str, str],
) -> list[dict]:
    """Return a copy of claims with evidence_ids, evidence_labels attached."""
    result: list[dict] = []
    for claim in claims:
        new_claim = dict(claim)
        evidence_ids: list[str] = []
        evidence_labels: list[str] = []
        for src in claim.get("evidence_sources", []) or []:
            key = _SOURCE_ALIASES.get(src, src)
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
        result.append(new_claim)
    return result


# ---------------------------------------------------------------------------
# Phase 4: final context assembly
# ---------------------------------------------------------------------------

def build_report_context(state: InvestigationState) -> ReportContext:
    """Build the full ReportContext from an InvestigationState.

    Internally runs four distinct phases:
    1. Normalize/extract raw data from state.
    2. Build the evidence catalog per source.
    3. Attach catalog references to claims.
    4. Assemble the final dict.
    """
    ns = _NormalizedState(state)
    catalog, source_to_id = _build_evidence_catalog(ns)
    display_map = {eid: entry.get("display_id", eid) for eid, entry in catalog.items()}
    validated_claims = _attach_evidence_to_claims(ns.validated_claims, source_to_id, display_map)
    non_validated_claims = _attach_evidence_to_claims(ns.non_validated_claims, source_to_id, display_map)

    return {
        # Core RCA results
        "pipeline_name": state.get("pipeline_name", "unknown"),
        "alert_name": state.get("alert_name"),
        "root_cause": state.get("root_cause", ""),
        "root_cause_category": state.get("root_cause_category"),
        "validated_claims": validated_claims,
        "non_validated_claims": non_validated_claims,
        "validity_score": state.get("validity_score", 0.0),
        "investigation_recommendations": state.get("investigation_recommendations", []),
        "remediation_steps": state.get("remediation_steps", []),
        # S3 verification
        "s3_marker_exists": ns.s3.get("marker_exists", False),
        # Tracer web run metadata
        "tracer_run_status": ns.web_run.get("status"),
        "tracer_run_name": ns.web_run.get("run_name"),
        "tracer_pipeline_name": ns.web_run.get("pipeline_name"),
        "tracer_run_cost": ns.web_run.get("run_cost", 0),
        "tracer_max_ram_gb": ns.web_run.get("max_ram_gb", 0),
        "tracer_user_email": ns.web_run.get("user_email"),
        "tracer_team": ns.web_run.get("team"),
        "tracer_instance_type": ns.web_run.get("instance_type"),
        "tracer_failed_tasks": len(ns.evidence.get("failed_jobs", [])),
        # AWS Batch metadata
        "batch_failure_reason": ns.batch.get("failure_reason"),
        "batch_failed_jobs": ns.batch.get("failed_jobs", 0),
        # CloudWatch metadata
        "cloudwatch_log_group": ns.cloudwatch_group,
        "cloudwatch_log_stream": ns.cloudwatch_stream,
        "cloudwatch_logs_url": ns.cloudwatch_url,
        "cloudwatch_region": ns.cloudwatch_region,
        "alert_id": ns.alert_id,
        "evidence_catalog": catalog,
        "investigation_duration_seconds": ns.duration_seconds,
        # Raw data for deeper inspection
        "evidence": ns.evidence,
        "raw_alert": ns.raw_alert,
        # Tool call history for investigation transparency
        "executed_hypotheses": state.get("executed_hypotheses", []),
        # Integration endpoints for deep links
        "grafana_endpoint": ns.grafana_endpoint,
        "datadog_site": ns.datadog_site,
        # Kubernetes pod details — from Datadog evidence first, then alert annotations
        "kube_pod_name": (
            ns.evidence.get("datadog_pod_name")
            or _safe_get(ns.raw_alert, "annotations", "hostname")
            or _safe_get(ns.raw_alert, "commonAnnotations", "hostname")
        ),
        "kube_container_name": (
            ns.evidence.get("datadog_container_name")
            or _safe_get(ns.raw_alert, "annotations", "container_name")
            or _safe_get(ns.raw_alert, "commonAnnotations", "container_name")
        ),
        "kube_namespace": (
            ns.evidence.get("datadog_kube_namespace")
            or _safe_get(ns.raw_alert, "annotations", "namespace")
            or _safe_get(ns.raw_alert, "commonAnnotations", "namespace")
            or _safe_get(ns.raw_alert, "annotations", "kube_namespace")
        ),
        # Multiple failed pods — populated from Datadog evidence when available
        "kube_failed_pods": ns.evidence.get("datadog_failed_pods", []),
    }
