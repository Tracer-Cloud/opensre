"""
Centralized schema definitions for synthetic testing fixtures.

All scenario fixture files (alert.json, cloudwatch_metrics.json, rds_events.json,
performance_insights.json, answer.yml) must conform to these TypedDicts.
Validators enforce required fields so every scenario is structurally consistent.
"""

from __future__ import annotations

from typing import Any, NotRequired

from typing_extensions import TypedDict

# ---------------------------------------------------------------------------
# Alert fixture  (alert.json)
# ---------------------------------------------------------------------------


class AlertLabels(TypedDict, total=False):
    alertname: str
    severity: str
    pipeline_name: str
    service: str
    engine: str


class AlertAnnotations(TypedDict, total=False):
    summary: str
    error: str
    suspected_symptom: str
    db_instance_identifier: str
    db_instance: str
    db_cluster: str
    read_replica: str
    cloudwatch_region: str
    rds_failure_mode: str
    context_sources: str


class AlertFixture(TypedDict):
    title: str
    state: str
    alert_source: str
    commonLabels: AlertLabels
    commonAnnotations: AlertAnnotations


# ---------------------------------------------------------------------------
# CloudWatch metrics fixture  (cloudwatch_metrics.json)
# ---------------------------------------------------------------------------


class MetricDatapoint(TypedDict, total=False):
    timestamp: str
    maximum: float
    minimum: float
    average: float
    sum: float
    sample_count: float


class MetricRecord(TypedDict):
    metric_name: str
    unit: str
    summary: str
    recent_datapoints: list[MetricDatapoint]


class CloudWatchMetricsFixture(TypedDict):
    db_instance_identifier: str
    observations: list[str]
    metrics: list[MetricRecord]


# ---------------------------------------------------------------------------
# RDS events fixture  (rds_events.json)
# ---------------------------------------------------------------------------


class RDSEvent(TypedDict):
    date: str
    message: str
    source_identifier: str
    source_type: str


class RDSEventsFixture(TypedDict):
    events: list[RDSEvent]


# ---------------------------------------------------------------------------
# Performance insights fixture  (performance_insights.json)
# ---------------------------------------------------------------------------


class TopSQL(TypedDict):
    sql: str
    db_load: float
    wait_event: str


class WaitEvent(TypedDict):
    name: str
    db_load: float


class PerformanceInsightsFixture(TypedDict):
    db_instance_identifier: str
    observations: list[str]
    top_sql: list[TopSQL]
    wait_events: list[WaitEvent]


# ---------------------------------------------------------------------------
# Answer key  (answer.yml)
# ---------------------------------------------------------------------------


class AnswerKeySchema(TypedDict):
    root_cause_category: str
    required_keywords: list[str]
    model_response: str


# ---------------------------------------------------------------------------
# Validators — raise ValueError with a descriptive message on bad data
# ---------------------------------------------------------------------------


def validate_alert(data: dict[str, Any]) -> AlertFixture:
    _require_str(data, "title", ctx="alert.json")
    _require_str(data, "state", ctx="alert.json")
    _require_str(data, "alert_source", ctx="alert.json")
    if not isinstance(data.get("commonLabels"), dict):
        raise ValueError("alert.json: 'commonLabels' must be an object")
    if not isinstance(data.get("commonAnnotations"), dict):
        raise ValueError("alert.json: 'commonAnnotations' must be an object")
    return data  # type: ignore[return-value]


def validate_cloudwatch_metrics(data: dict[str, Any]) -> CloudWatchMetricsFixture:
    _require_str(data, "db_instance_identifier", ctx="cloudwatch_metrics.json")
    if not isinstance(data.get("observations"), list):
        raise ValueError("cloudwatch_metrics.json: 'observations' must be a list")
    if not isinstance(data.get("metrics"), list):
        raise ValueError("cloudwatch_metrics.json: 'metrics' must be a list")
    for i, metric in enumerate(data["metrics"]):
        ctx = f"cloudwatch_metrics.json:metrics[{i}]"
        _require_str(metric, "metric_name", ctx=ctx)
        _require_str(metric, "unit", ctx=ctx)
        _require_str(metric, "summary", ctx=ctx)
        if not isinstance(metric.get("recent_datapoints"), list):
            raise ValueError(f"{ctx}: 'recent_datapoints' must be a list")
    return data  # type: ignore[return-value]


def validate_rds_events(data: dict[str, Any]) -> RDSEventsFixture:
    if not isinstance(data.get("events"), list):
        raise ValueError("rds_events.json: 'events' must be a list")
    for i, event in enumerate(data["events"]):
        ctx = f"rds_events.json:events[{i}]"
        _require_str(event, "date", ctx=ctx)
        _require_str(event, "message", ctx=ctx)
        _require_str(event, "source_identifier", ctx=ctx)
        _require_str(event, "source_type", ctx=ctx)
    return data  # type: ignore[return-value]


def validate_performance_insights(data: dict[str, Any]) -> PerformanceInsightsFixture:
    _require_str(data, "db_instance_identifier", ctx="performance_insights.json")
    if not isinstance(data.get("observations"), list):
        raise ValueError("performance_insights.json: 'observations' must be a list")
    if not isinstance(data.get("top_sql"), list):
        raise ValueError("performance_insights.json: 'top_sql' must be a list")
    if not isinstance(data.get("wait_events"), list):
        raise ValueError("performance_insights.json: 'wait_events' must be a list")
    return data  # type: ignore[return-value]


def validate_answer_key(data: dict[str, Any]) -> AnswerKeySchema:
    _require_str(data, "root_cause_category", ctx="answer.yml")
    keywords = data.get("required_keywords")
    if not isinstance(keywords, list) or not keywords:
        raise ValueError("answer.yml: 'required_keywords' must be a non-empty list")
    if not all(isinstance(k, str) and k.strip() for k in keywords):
        raise ValueError("answer.yml: all required_keywords must be non-empty strings")
    _require_str(data, "model_response", ctx="answer.yml")
    return data  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _require_str(obj: dict[str, Any], key: str, ctx: str = "") -> None:
    value = obj.get(key)
    prefix = f"{ctx}: " if ctx else ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{prefix}missing or empty required string field '{key}'")
