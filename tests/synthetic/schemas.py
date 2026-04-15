"""
Centralized schema definitions for synthetic testing fixtures.

All scenario fixture files (alert.json, aws_cloudwatch_metrics.json, aws_rds_events.json,
aws_performance_insights.json, answer.yml, scenario.yml) must conform to these TypedDicts.
Validators enforce required fields so every scenario is structurally consistent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, NotRequired

from typing_extensions import TypedDict

# ---------------------------------------------------------------------------
# Controlled vocabularies for scenario metadata
# ---------------------------------------------------------------------------

VALID_ENGINES = frozenset({"postgres", "mysql", "aurora-postgres", "aurora-mysql", "mariadb"})
VALID_FAILURE_MODES = frozenset(
    {"replication_lag", "connection_exhaustion", "storage_full", "cpu_saturation", "failover", "healthy"}
)
VALID_EVIDENCE_SOURCES = frozenset({"aws_cloudwatch_metrics", "aws_rds_events", "aws_performance_insights"})

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
# CloudWatch metrics fixture  (aws_cloudwatch_metrics.json)
# Models the AWS GetMetricData API response shape.
# ---------------------------------------------------------------------------


class MetricDimension(TypedDict):
    Name: str
    Value: str


class MetricDataResult(TypedDict):
    """One metric query result, combining query context with response data."""

    id: str
    label: str
    metric_name: str
    dimensions: list[MetricDimension]
    stat: str
    unit: str
    status_code: str
    timestamps: list[str]
    values: list[float]


class CloudWatchMetricsFixture(TypedDict):
    namespace: str
    period: int
    start_time: str
    end_time: str
    metric_data_results: list[MetricDataResult]


# ---------------------------------------------------------------------------
# RDS events fixture  (aws_rds_events.json)
# Models the AWS DescribeEvents API response shape.
# ---------------------------------------------------------------------------


class RDSEvent(TypedDict):
    date: str
    message: str
    source_identifier: str
    source_type: str
    event_categories: list[str]


class RDSEventsFixture(TypedDict):
    events: list[RDSEvent]


# ---------------------------------------------------------------------------
# Performance insights fixture  (aws_performance_insights.json)
# Models the AWS GetResourceMetrics + DescribeDimensionKeys API response shape.
# ---------------------------------------------------------------------------


class DBLoadTimeSeries(TypedDict):
    timestamps: list[str]
    values: list[float]
    unit: str


class TopSQLWaitEvent(TypedDict):
    name: str
    type: str
    db_load_avg: float


class TopSQL(TypedDict):
    statement: str
    db_load_avg: float
    wait_events: list[TopSQLWaitEvent]
    calls_per_sec: float


class TopWaitEvent(TypedDict):
    name: str
    type: str
    db_load_avg: float


class TopUser(TypedDict):
    name: str
    db_load_avg: float


class TopHost(TypedDict):
    id: str
    db_load_avg: float


class PerformanceInsightsFixture(TypedDict):
    db_instance_identifier: str
    start_time: str
    end_time: str
    db_load: DBLoadTimeSeries
    top_sql: list[TopSQL]
    top_wait_events: list[TopWaitEvent]
    top_users: list[TopUser]
    top_hosts: list[TopHost]


# ---------------------------------------------------------------------------
# Answer key  (answer.yml)
# ---------------------------------------------------------------------------


VALID_TRAJECTORY_ACTIONS = frozenset(
    {"query_grafana_metrics", "query_grafana_logs", "query_grafana_alert_rules"}
)


class AnswerKeySchema(TypedDict):
    root_cause_category: str
    required_keywords: list[str]
    model_response: str
    # Optional adversarial constraints (level 2+ scenarios)
    forbidden_categories: NotRequired[list[str]]      # root_cause_category must NOT be any of these
    forbidden_keywords: NotRequired[list[str]]         # none of these may appear in evidence_text
    required_evidence_sources: NotRequired[list[str]]  # these keys must be non-empty in final_state["evidence"]
    # Trajectory efficiency (Axis 1)
    optimal_trajectory: NotRequired[list[str]]        # ordered action names the agent should call
    max_investigation_loops: NotRequired[int]          # how many investigation loops is acceptable
    # Adversarial reasoning (Axis 2)
    ruling_out_keywords: NotRequired[list[str]]       # agent output must contain these tokens (proof it dismissed alternatives)
    required_queries: NotRequired[list[str]]           # metric names agent must have specifically requested via query_timeseries


# ---------------------------------------------------------------------------
# Scenario metadata  (scenario.yml)
# ---------------------------------------------------------------------------


class ScenarioMetadataSchema(TypedDict):
    schema_version: str
    scenario_id: str
    engine: str
    engine_version: str
    instance_class: str
    region: str
    db_instance_identifier: str
    failure_mode: str
    severity: str
    available_evidence: list[str]
    db_cluster: NotRequired[str]
    scenario_difficulty: NotRequired[int]        # 1–4 curriculum level
    adversarial_signals: NotRequired[list[str]]  # metrics that are intentional confounders
    depends_on: NotRequired[str]                 # e.g. "healthy_rca_state" — CI skip flag


# ---------------------------------------------------------------------------
# Typed evidence container
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScenarioEvidence:
    """Typed container for all evidence sources in a scenario fixture.

    Each attribute is None when the corresponding file was not listed in
    scenario.yml:available_evidence, making evidence presence explicit.
    """

    aws_cloudwatch_metrics: CloudWatchMetricsFixture | None
    aws_rds_events: list[RDSEvent] | None
    aws_performance_insights: PerformanceInsightsFixture | None

    def as_dict(self) -> dict[str, Any]:
        """Return only the non-None sources as a plain dict."""
        result: dict[str, Any] = {}
        if self.aws_cloudwatch_metrics is not None:
            result["aws_cloudwatch_metrics"] = self.aws_cloudwatch_metrics
        if self.aws_rds_events is not None:
            result["aws_rds_events"] = self.aws_rds_events
        if self.aws_performance_insights is not None:
            result["aws_performance_insights"] = self.aws_performance_insights
        return result

    def get(self, key: str) -> Any:
        return self.as_dict().get(key)


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
    ctx = "aws_cloudwatch_metrics.json"
    _require_str(data, "namespace", ctx=ctx)
    _require_str(data, "start_time", ctx=ctx)
    _require_str(data, "end_time", ctx=ctx)
    if not isinstance(data.get("period"), int):
        raise ValueError(f"{ctx}: 'period' must be an integer (seconds)")
    results = data.get("metric_data_results")
    if not isinstance(results, list) or not results:
        raise ValueError(f"{ctx}: 'metric_data_results' must be a non-empty list")
    for i, result in enumerate(results):
        rctx = f"{ctx}:metric_data_results[{i}]"
        for field in ("id", "label", "metric_name", "stat", "unit", "status_code"):
            _require_str(result, field, ctx=rctx)
        if not isinstance(result.get("dimensions"), list):
            raise ValueError(f"{rctx}: 'dimensions' must be a list")
        for dim in result["dimensions"]:
            _require_str(dim, "Name", ctx=rctx)
            _require_str(dim, "Value", ctx=rctx)
        if not isinstance(result.get("timestamps"), list):
            raise ValueError(f"{rctx}: 'timestamps' must be a list")
        if not isinstance(result.get("values"), list):
            raise ValueError(f"{rctx}: 'values' must be a list")
        if len(result["timestamps"]) != len(result["values"]):
            raise ValueError(f"{rctx}: 'timestamps' and 'values' must have the same length")
    return data  # type: ignore[return-value]


def validate_rds_events(data: dict[str, Any]) -> RDSEventsFixture:
    if not isinstance(data.get("events"), list):
        raise ValueError("aws_rds_events.json: 'events' must be a list")
    for i, event in enumerate(data["events"]):
        ctx = f"aws_rds_events.json:events[{i}]"
        _require_str(event, "date", ctx=ctx)
        _require_str(event, "message", ctx=ctx)
        _require_str(event, "source_identifier", ctx=ctx)
        _require_str(event, "source_type", ctx=ctx)
        if not isinstance(event.get("event_categories"), list):
            raise ValueError(f"{ctx}: 'event_categories' must be a list")
    return data  # type: ignore[return-value]


def validate_performance_insights(data: dict[str, Any]) -> PerformanceInsightsFixture:
    ctx = "aws_performance_insights.json"
    _require_str(data, "db_instance_identifier", ctx=ctx)
    _require_str(data, "start_time", ctx=ctx)
    _require_str(data, "end_time", ctx=ctx)
    db_load = data.get("db_load")
    if not isinstance(db_load, dict):
        raise ValueError(f"{ctx}: 'db_load' must be an object")
    if not isinstance(db_load.get("timestamps"), list):
        raise ValueError(f"{ctx}: 'db_load.timestamps' must be a list")
    if not isinstance(db_load.get("values"), list):
        raise ValueError(f"{ctx}: 'db_load.values' must be a list")
    if len(db_load["timestamps"]) != len(db_load["values"]):
        raise ValueError(f"{ctx}: 'db_load.timestamps' and 'db_load.values' must have the same length")
    if not isinstance(data.get("top_sql"), list):
        raise ValueError(f"{ctx}: 'top_sql' must be a list")
    if not isinstance(data.get("top_wait_events"), list):
        raise ValueError(f"{ctx}: 'top_wait_events' must be a list")
    if not isinstance(data.get("top_users"), list):
        raise ValueError(f"{ctx}: 'top_users' must be a list")
    if not isinstance(data.get("top_hosts"), list):
        raise ValueError(f"{ctx}: 'top_hosts' must be a list")
    return data  # type: ignore[return-value]


def validate_answer_key(data: dict[str, Any]) -> AnswerKeySchema:
    _require_str(data, "root_cause_category", ctx="answer.yml")
    _require_non_empty_str_list(data, "required_keywords", "answer.yml", required=True)
    _require_str(data, "model_response", ctx="answer.yml")
    for opt_list_field in ("forbidden_categories", "forbidden_keywords", "required_evidence_sources"):
        val = data.get(opt_list_field)
        if val is not None and not isinstance(val, list):
            raise ValueError(f"answer.yml: '{opt_list_field}' must be a list when present")
    trajectory = data.get("optimal_trajectory")
    if trajectory is not None:
        if not isinstance(trajectory, list) or not trajectory:
            raise ValueError("answer.yml: 'optimal_trajectory' must be a non-empty list when present")
        unknown_actions = [a for a in trajectory if a not in VALID_TRAJECTORY_ACTIONS]
        if unknown_actions:
            raise ValueError(
                f"answer.yml: unknown action(s) in optimal_trajectory {unknown_actions}; "
                f"expected subset of {sorted(VALID_TRAJECTORY_ACTIONS)}"
            )
    max_loops = data.get("max_investigation_loops")
    if max_loops is not None and (not isinstance(max_loops, int) or max_loops < 1):
        raise ValueError("answer.yml: 'max_investigation_loops' must be a positive integer when present")
    for axis2_list_field in ("ruling_out_keywords", "required_queries"):
        _require_non_empty_str_list(data, axis2_list_field, "answer.yml")
    return data  # type: ignore[return-value]


def validate_scenario_metadata(data: dict[str, Any]) -> ScenarioMetadataSchema:
    ctx = "scenario.yml"
    for field in ("schema_version", "scenario_id", "engine", "engine_version", "instance_class", "region", "db_instance_identifier", "failure_mode", "severity"):
        _require_str(data, field, ctx=ctx)

    engine = data["engine"]
    if engine not in VALID_ENGINES:
        raise ValueError(f"{ctx}: unknown engine {engine!r}; expected one of {sorted(VALID_ENGINES)}")

    failure_mode = data["failure_mode"]
    if failure_mode not in VALID_FAILURE_MODES:
        raise ValueError(f"{ctx}: unknown failure_mode {failure_mode!r}; expected one of {sorted(VALID_FAILURE_MODES)}")

    sources = data.get("available_evidence")
    if not isinstance(sources, list) or not sources:
        raise ValueError(f"{ctx}: 'available_evidence' must be a non-empty list")
    unknown = [s for s in sources if s not in VALID_EVIDENCE_SOURCES]
    if unknown:
        raise ValueError(f"{ctx}: unknown evidence source(s) {unknown}; expected subset of {sorted(VALID_EVIDENCE_SOURCES)}")

    return data  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _require_str(obj: dict[str, Any], key: str, ctx: str = "") -> None:
    value = obj.get(key)
    prefix = f"{ctx}: " if ctx else ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{prefix}missing or empty required string field '{key}'")

def _require_non_empty_str_list(
    obj: dict[str, Any],
    key: str,
    ctx: str,
    *,
    required: bool = False,
) -> None:
    value = obj.get(key)

    if value is None:
        if required:
            raise ValueError(f"{ctx}: '{key}' must be a non-empty list")
        return

    if not isinstance(value, list) or not value:
        raise ValueError(f"{ctx}: '{key}' must be a non-empty list")

    if not all(isinstance(item, str) and item.strip() for item in value):
        raise ValueError(f"{ctx}: all '{key}' entries must be non-empty strings")
