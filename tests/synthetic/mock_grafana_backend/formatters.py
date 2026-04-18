"""
Static mock Grafana formatter functions.

Each function converts an existing AWS-faithful fixture dict into the exact
JSON envelope that the corresponding real Grafana datasource proxy endpoint
would return.  No HTTP server is required — tests can call these directly.

Endpoints modelled:
    Mimir  /api/v1/query_range          ← aws_cloudwatch_metrics.json
    Loki   /loki/api/v1/query_range     ← aws_rds_events.json
    Ruler  /api/v1/rules                ← alert.json
    Tempo  /api/search                  ← (empty — RDS scenarios have no traces)
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _iso_to_unix(ts: str) -> float:
    """Parse an ISO-8601 UTC timestamp string and return a Unix epoch float."""
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC).timestamp() if dt.tzinfo is None else dt.timestamp()


def _iso_to_unix_ns(ts: str) -> str:
    """Parse an ISO-8601 UTC timestamp string and return nanosecond Unix epoch as a string.

    Loki expects log entry timestamps as nanosecond-precision Unix epoch strings.
    """
    return str(int(_iso_to_unix(ts) * 1_000_000_000))


def _metric_name(metric_name: str, stat: str) -> str:
    """Build a Prometheus-style metric name from an AWS metric name and stat.

    Examples:
        ReplicaLag, Maximum  → aws_rds_replica_lag_maximum
        CPUUtilization, Average → aws_rds_cpuutilization_average
        WriteIOPS, Average → aws_rds_write_iops_average
    """
    # Insert underscore before uppercase runs that follow lowercase letters
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", metric_name)
    snake = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", snake)
    return f"aws_rds_{snake.lower()}_{stat.lower()}"


def _dimension_labels(dimensions: list[dict[str, str]]) -> dict[str, str]:
    """Convert AWS Dimension objects to lowercase Prometheus label names."""
    return {d["Name"].lower(): d["Value"] for d in dimensions if "Name" in d and "Value" in d}


# ---------------------------------------------------------------------------
# Mimir / Prometheus
# ---------------------------------------------------------------------------


def format_mimir_query_range(cw_fixture: dict[str, Any]) -> dict[str, Any]:
    """Convert a CloudWatch GetMetricData fixture → Mimir query_range response.

    Each metric_data_results entry becomes one Prometheus matrix series.
    Metric labels: lowercase dimension names (e.g. dbinstanceidentifier).
    Values follow Prometheus convention: [unix_epoch_float, "string_value"].

    Args:
        cw_fixture: Parsed aws_cloudwatch_metrics.json content matching
                    CloudWatchMetricsFixture schema.

    Returns:
        Dict matching the Mimir /api/v1/query_range success envelope.
    """
    result_series: list[dict[str, Any]] = []

    for entry in cw_fixture.get("metric_data_results", []):
        name = _metric_name(entry.get("metric_name", "unknown"), entry.get("stat", "average"))
        labels: dict[str, str] = {"__name__": name}
        labels.update(_dimension_labels(entry.get("dimensions", [])))

        timestamps: list[str] = entry.get("timestamps", [])
        values: list[float] = entry.get("values", [])

        prom_values = [
            [_iso_to_unix(ts), str(v)]
            for ts, v in zip(timestamps, values)
        ]

        result_series.append({"metric": labels, "values": prom_values})

    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": result_series,
        },
    }


# ---------------------------------------------------------------------------
# Loki
# ---------------------------------------------------------------------------


def format_loki_query_range(rds_events_fixture: dict[str, Any]) -> dict[str, Any]:
    """Convert an RDS Events fixture → Loki query_range response.

    Each event becomes a separate log stream keyed by (source_type,
    source_identifier).  Events sharing the same key are grouped into a single
    stream entry with multiple log lines, matching real Loki behaviour.

    Args:
        rds_events_fixture: Parsed aws_rds_events.json content matching
                            RDSEventsFixture schema.

    Returns:
        Dict matching the Loki /loki/api/v1/query_range success envelope.
    """
    # Group events by (source_type, source_identifier) to mirror Loki streams
    stream_map: dict[tuple[str, str], list[list[str]]] = {}

    for event in rds_events_fixture.get("events", []):
        key = (event.get("source_type", ""), event.get("source_identifier", ""))
        ns_ts = _iso_to_unix_ns(event["date"])
        line = event.get("message", "")
        stream_map.setdefault(key, []).append([ns_ts, line])

    # Sort log lines within each stream by ascending timestamp
    loki_result: list[dict[str, Any]] = []
    for (source_type, source_identifier), log_lines in stream_map.items():
        log_lines.sort(key=lambda x: x[0])
        loki_result.append({
            "stream": {
                "source_type": source_type,
                "source_identifier": source_identifier,
            },
            "values": log_lines,
        })

    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": loki_result,
        },
    }


# ---------------------------------------------------------------------------
# Ruler / Alertmanager
# ---------------------------------------------------------------------------


def format_tempo_search() -> dict[str, Any]:
    """Return an empty Tempo /api/search response.

    RDS synthetic scenarios do not include trace fixture data, so the mock
    returns a structurally valid but empty response.  This allows the traces
    tool to report ``available=True`` with zero traces instead of failing
    with "Grafana integration not configured".
    """
    return {
        "traces": [],
        "metrics": {},
    }


def format_ruler_rules(alert_fixture: dict[str, Any]) -> dict[str, Any]:
    """Convert an alert fixture → Grafana Ruler /api/v1/rules response.

    The alert state is mapped to "firing" / "inactive".  Labels and annotations
    are passed through unchanged.  The rule group name is derived from
    commonLabels.pipeline_name, falling back to "synthetic".

    Args:
        alert_fixture: Parsed alert.json content matching AlertFixture schema.

    Returns:
        Dict matching the Grafana Ruler /api/v1/rules success envelope.
    """
    labels: dict[str, str] = dict(alert_fixture.get("commonLabels", {}))
    annotations: dict[str, str] = dict(alert_fixture.get("commonAnnotations", {}))

    alert_name = labels.get("alertname", alert_fixture.get("title", "UnknownAlert"))
    group_name = labels.get("pipeline_name", "synthetic")

    grafana_state = "firing" if alert_fixture.get("state", "") == "alerting" else "inactive"

    rule: dict[str, Any] = {
        "state": grafana_state,
        "name": alert_name,
        "labels": labels,
        "annotations": annotations,
    }

    return {
        "groups": [
            {
                "name": group_name,
                "rules": [rule],
            }
        ]
    }
