"""
Adapts AWS-faithful fixture evidence into the shape that the production
prompt_builder expects.

The fixtures (cloudwatch_metrics.json, performance_insights.json, rds_events.json)
are modelled on real AWS API response shapes (GetMetricData, DescribeDimensionKeys,
DescribeEvents). The production prompt_builder reads a simpler internal format
with pre-computed summaries. This module bridges the two without touching
production code.

Adapter functions:
    adapt_cloudwatch_metrics   — GetMetricData shape → prompt_builder's "metrics" shape
    adapt_performance_insights — DescribeDimensionKeys shape → prompt_builder's PI shape
    adapt_rds_events           — DescribeEvents shape (list) → same list, stripped of
                                 simulation-only fields (event_categories)
    adapt_evidence_for_prompt  — top-level convenience wrapper
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# CloudWatch metrics
# ---------------------------------------------------------------------------

_BYTE_UNITS = frozenset({"Bytes", "Bytes/Second"})
_BYTE_DIVISORS = [
    (1 << 30, "GB"),
    (1 << 20, "MB"),
    (1 << 10, "KB"),
]


def _fmt(value: float, unit: str) -> str:
    """Human-readable value string, converting raw bytes to a sensible unit."""
    if unit in _BYTE_UNITS:
        for divisor, label in _BYTE_DIVISORS:
            if value >= divisor:
                suffix = unit.replace("Bytes", label)
                return f"{value / divisor:.1f} {suffix}"
    return f"{value:g} {unit}".strip()


def adapt_cloudwatch_metrics(cw: dict[str, Any]) -> dict[str, Any]:
    """Convert GetMetricData-shaped fixture → prompt_builder's expected shape.

    Output shape:
        {
          "db_instance_identifier": str,   # derived from first result's dimensions
          "observations": [str, ...],       # one narrative sentence per metric
          "metrics": [{
              "metric_name": str,
              "unit": str,
              "summary": str,
              "recent_datapoints": [{"timestamp": str, "value": float}, ...]
          }]
        }
    """
    results: list[dict[str, Any]] = cw.get("metric_data_results", [])
    period = cw.get("period", 60)

    # Derive a primary db_instance_identifier from the first result's dimensions
    db_instance = ""
    for r in results:
        for dim in r.get("dimensions", []):
            if isinstance(dim, dict) and dim.get("Name") == "DBInstanceIdentifier":
                db_instance = dim.get("Value", "")
                break
        if db_instance:
            break

    metrics: list[dict[str, Any]] = []
    observations: list[str] = []

    for r in results:
        if not isinstance(r, dict):
            continue

        metric_name = r.get("label") or r.get("metric_name", "unknown")
        unit = r.get("unit", "")
        stat = r.get("stat", "Average")
        dimensions = r.get("dimensions", [])
        timestamps: list[str] = r.get("timestamps", [])
        values: list[float] = r.get("values", [])

        # Build a dimension qualifier (e.g. "on payments-prod-replica-1")
        dim_parts = [
            f"{d['Value']}"
            for d in dimensions
            if isinstance(d, dict) and d.get("Name") == "DBInstanceIdentifier"
        ]
        instance_qualifier = f" on {dim_parts[0]}" if dim_parts else ""

        # Derive a concise summary sentence from min/max/last
        if values:
            min_v = min(values)
            max_v = max(values)
            last_v = values[-1]
            window_mins = len(values) * period // 60
            summary = (
                f"{metric_name}{instance_qualifier}: "
                f"min {_fmt(min_v, unit)}, max {_fmt(max_v, unit)}, "
                f"last {_fmt(last_v, unit)} "
                f"({stat}, {window_mins}-min window, {len(values)} points)"
            )
        else:
            summary = f"{metric_name}{instance_qualifier}: no data"

        recent_datapoints = [
            {"timestamp": ts, "value": v}
            for ts, v in zip(timestamps[-5:], values[-5:])
        ]

        metrics.append({
            "metric_name": metric_name,
            "unit": unit,
            "summary": summary,
            "recent_datapoints": recent_datapoints,
        })

        if values:
            observations.append(summary)

    return {
        "db_instance_identifier": db_instance,
        "observations": observations,
        "metrics": metrics,
    }


# ---------------------------------------------------------------------------
# Performance Insights
# ---------------------------------------------------------------------------


def adapt_performance_insights(pi: dict[str, Any]) -> dict[str, Any]:
    """Convert DescribeDimensionKeys-shaped fixture → prompt_builder's expected shape.

    Output shape:
        {
          "db_instance_identifier": str,
          "observations": [str, ...],
          "top_sql": [{"sql": str, "db_load": float, "wait_event": str}],
          "wait_events": [{"name": str, "db_load": float}]
        }
    """
    db_instance = pi.get("db_instance_identifier", "")
    observations: list[str] = []

    # DB load summary observation
    db_load_ts = pi.get("db_load", {})
    if isinstance(db_load_ts, dict) and db_load_ts.get("values"):
        vals = db_load_ts["values"]
        avg = sum(vals) / len(vals)
        observations.append(
            f"Average active sessions (DB load): min {min(vals):.1f}, "
            f"max {max(vals):.1f}, avg {avg:.1f} over the incident window."
        )

    # Derive top_sql in the legacy shape
    top_sql: list[dict[str, Any]] = []
    for item in pi.get("top_sql", []):
        if not isinstance(item, dict):
            continue
        sql = str(item.get("statement") or item.get("sql", ""))
        db_load = item.get("db_load_avg") or item.get("db_load", 0.0)
        wait_events_list = item.get("wait_events", [])
        top_wait = (
            wait_events_list[0].get("name", "")
            if wait_events_list and isinstance(wait_events_list[0], dict)
            else item.get("wait_event", "")
        )
        top_sql.append({"sql": sql, "db_load": db_load, "wait_event": top_wait})

    # Derive wait_events in the legacy shape (from top_wait_events or wait_events)
    raw_wait = pi.get("top_wait_events") or pi.get("wait_events", [])
    wait_events: list[dict[str, Any]] = []
    for item in raw_wait:
        if not isinstance(item, dict):
            continue
        db_load = item.get("db_load_avg") or item.get("db_load", 0.0)
        wait_events.append({"name": item.get("name", "unknown"), "db_load": db_load})

    # Observation from top wait event
    if wait_events:
        top = wait_events[0]
        observations.append(
            f"Dominant wait event: {top['name']} (db_load_avg={top['db_load']})."
        )

    # Observation from top SQL
    if top_sql:
        top = top_sql[0]
        observations.append(
            f"Top SQL by DB load ({top['db_load']}): "
            f"\"{top['sql'][:120]}\" — waiting on {top['wait_event']}."
        )

    return {
        "db_instance_identifier": db_instance,
        "observations": observations,
        "top_sql": top_sql,
        "wait_events": wait_events,
    }


# ---------------------------------------------------------------------------
# RDS Events
# ---------------------------------------------------------------------------


def adapt_rds_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Strip simulation-only fields (event_categories) from each event.

    The production prompt_builder ignores unknown keys, but keeping the adapter
    explicit makes the boundary clear.
    """
    adapted = []
    for event in events:
        if not isinstance(event, dict):
            continue
        adapted.append({
            "date": event.get("date", ""),
            "message": event.get("message", ""),
            "source_identifier": event.get("source_identifier", ""),
            "source_type": event.get("source_type", ""),
        })
    return adapted


# ---------------------------------------------------------------------------
# Top-level convenience
# ---------------------------------------------------------------------------


def adapt_evidence_for_prompt(evidence: dict[str, Any]) -> dict[str, Any]:
    """Translate a full evidence dict from AWS-faithful fixture shape to the
    shape expected by the production prompt_builder.

    Only RDS-specific keys are transformed; all other keys pass through unchanged.
    """
    adapted = dict(evidence)

    if "rds_metrics" in adapted and isinstance(adapted["rds_metrics"], dict):
        adapted["rds_metrics"] = adapt_cloudwatch_metrics(adapted["rds_metrics"])

    if "performance_insights" in adapted and isinstance(adapted["performance_insights"], dict):
        adapted["performance_insights"] = adapt_performance_insights(
            adapted["performance_insights"]
        )

    if "rds_events" in adapted and isinstance(adapted["rds_events"], list):
        adapted["rds_events"] = adapt_rds_events(adapted["rds_events"])

    return adapted
