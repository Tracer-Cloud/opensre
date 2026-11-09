"""Map Grafana tool output dicts into investigation evidence keys."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

from infrastructure.evidence.metric_summary import summarize_prometheus_metrics


def _map_grafana_logs(data: dict[str, Any]) -> dict[str, Any]:
    logs = data.get("logs", [])
    mapped: dict[str, Any] = {
        "grafana_logs": data.get("logs", []),
        "grafana_error_logs": data.get("error_logs", []),
        "grafana_logs_query": data.get("query", ""),
        "grafana_logs_service": data.get("service_name", ""),
    }
    rds_events = _derive_rds_events_from_grafana_logs(logs)
    if rds_events:
        mapped["aws_rds_events"] = rds_events
    performance_insights = _derive_performance_insights_from_grafana_logs(logs)
    if performance_insights:
        mapped["aws_performance_insights"] = performance_insights
    for evidence_key, records in _derive_k8s_evidence_from_grafana_logs(logs).items():
        mapped[evidence_key] = records
    return mapped


def _map_grafana_metrics(data: dict[str, Any]) -> dict[str, Any]:
    metrics = data.get("metrics", [])
    summaries = summarize_prometheus_metrics(metrics)
    mapped: dict[str, Any] = {
        "grafana_metrics": metrics,
        "grafana_metric_summaries": summaries,
        "grafana_metric_name": data.get("metric_name", ""),
        "grafana_metrics_service": data.get("service_name", ""),
    }
    rds_metrics = _build_rds_cloudwatch_metrics(summaries)
    if rds_metrics:
        mapped["aws_cloudwatch_metrics"] = rds_metrics
    for evidence_key, payload in _build_k8s_metrics_evidence(summaries).items():
        mapped[evidence_key] = payload
    return mapped


def _map_grafana_traces(data: dict[str, Any]) -> dict[str, Any]:
    return {
        "grafana_traces": data.get("traces", []),
        "grafana_pipeline_spans": data.get("pipeline_spans", []),
        "grafana_traces_service": data.get("service_name", ""),
    }


def _timestamp_from_loki_ns(value: object) -> str:
    try:
        timestamp = int(float(str(value))) / 1_000_000_000
    except (TypeError, ValueError):
        return str(value or "")
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _derive_rds_events_from_grafana_logs(logs: list) -> list[dict]:
    events: list[dict] = []
    for log in logs:
        if not isinstance(log, dict):
            continue
        source_type = str(log.get("source_type", "")).lower()
        message = str(log.get("message", ""))
        if source_type != "db-instance" and "db instance" not in message.lower():
            continue
        events.append(
            {
                "timestamp": _timestamp_from_loki_ns(log.get("timestamp")),
                "message": message,
                "source_type": log.get("source_type"),
                "source_identifier": log.get("source_identifier"),
            }
        )
    return events


def _derive_performance_insights_from_grafana_logs(logs: list) -> dict:
    observations: list[str] = []
    top_sql: list[dict] = []
    wait_events: list[dict] = []
    for log in logs:
        if not isinstance(log, dict):
            continue
        source_type = str(log.get("source_type", "")).lower()
        message = str(log.get("message", ""))
        is_pi = source_type == "aws_performance_insights" or message.startswith(
            ("Top SQL Activity:", "Top Wait Event:")
        )
        if not is_pi:
            continue
        observations.append(message)
        sql_match = re.match(
            r"Top SQL Activity:\s*(?P<sql>.*?)\s*\|\s*Avg Load:\s*"
            r"(?P<load>[0-9.]+)\s*AAS\s*\|\s*Waits:\s*(?P<waits>.*)",
            message,
        )
        if sql_match:
            top_sql.append(
                {
                    "sql": sql_match.group("sql"),
                    "db_load": float(sql_match.group("load")),
                    "wait_event": sql_match.group("waits"),
                }
            )
            continue
        wait_match = re.match(
            r"Top Wait Event:\s*(?P<name>.*?)\s*\|\s*db_load_avg:\s*"
            r"(?P<load>[0-9.]+)\s*AAS",
            message,
        )
        if wait_match:
            wait_events.append(
                {
                    "name": wait_match.group("name"),
                    "db_load": float(wait_match.group("load")),
                }
            )
    if not observations:
        return {}
    return {"observations": observations, "top_sql": top_sql, "wait_events": wait_events}


_K8S_LOG_EVIDENCE_SOURCES = ("k8s_events", "k8s_rollout")


def _derive_k8s_evidence_from_grafana_logs(logs: list) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for log in logs:
        if not isinstance(log, dict):
            continue
        source_type = str(log.get("source_type", "")).strip()
        if source_type not in _K8S_LOG_EVIDENCE_SOURCES:
            continue
        grouped.setdefault(source_type, []).append(
            {
                "timestamp": _timestamp_from_loki_ns(log.get("timestamp")),
                "message": str(log.get("message", "")),
                "namespace": log.get("namespace", ""),
                "cluster": log.get("cluster", ""),
                "service": log.get("service", ""),
            }
        )
    return grouped


def _build_rds_cloudwatch_metrics(summaries: list[dict]) -> dict:
    rds_summaries = [
        s for s in summaries if str(s.get("raw_metric_name", "")).startswith("aws_rds_")
    ]
    if not rds_summaries:
        return {}
    db_instance = ""
    metrics: list[dict] = []
    observations: list[str] = []
    for summary in rds_summaries:
        labels = summary.get("labels", {})
        if isinstance(labels, dict) and not db_instance:
            db_instance = str(
                labels.get("dbinstanceidentifier")
                or labels.get("db_instance_identifier")
                or labels.get("db_instance")
                or ""
            )
        metrics.append(
            {
                "metric_name": summary.get("metric_name", "unknown"),
                "summary": summary.get("summary", ""),
                "labels": labels if isinstance(labels, dict) else {},
                "datapoint_count": summary.get("datapoint_count", 0),
            }
        )
        if summary.get("summary"):
            observations.append(str(summary["summary"]))
    return {"db_instance_identifier": db_instance, "metrics": metrics, "observations": observations}


_K8S_METRIC_EVIDENCE_SOURCES = (
    "k8s_pod_metrics",
    "k8s_node_metrics",
    "k8s_dns_metrics",
    "k8s_mesh_metrics",
)


def _build_k8s_metrics_evidence(summaries: list[dict]) -> dict[str, dict]:
    grouped: dict[str, dict] = {}
    for summary in summaries:
        labels = summary.get("labels", {})
        source = ""
        if isinstance(labels, dict):
            source = str(labels.get("source_type", ""))
        if not source:
            raw_name = str(summary.get("raw_metric_name", ""))
            for candidate in _K8S_METRIC_EVIDENCE_SOURCES:
                if raw_name.startswith(candidate):
                    source = candidate
                    break
        if source not in _K8S_METRIC_EVIDENCE_SOURCES:
            continue
        bucket = grouped.setdefault(source, {"metrics": [], "observations": []})
        bucket["metrics"].append(
            {
                "metric_name": summary.get("metric_name", "unknown"),
                "raw_metric_name": summary.get("raw_metric_name", ""),
                "labels": labels if isinstance(labels, dict) else {},
                "datapoint_count": summary.get("datapoint_count", 0),
                "summary": summary.get("summary", ""),
            }
        )
        if summary.get("summary"):
            bucket["observations"].append(str(summary["summary"]))
    return grouped
