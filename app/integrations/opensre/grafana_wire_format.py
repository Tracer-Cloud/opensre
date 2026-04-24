"""Grafana Mimir/Loki/Ruler wire-format envelopes (shared with synthetic fixtures)."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any


def _iso_to_unix(ts: str) -> float:
    dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    return dt.replace(tzinfo=UTC).timestamp() if dt.tzinfo is None else dt.timestamp()


def _iso_to_unix_ns(ts: str) -> str:
    return str(int(_iso_to_unix(ts) * 1_000_000_000))


def _metric_name(metric_name: str, stat: str) -> str:
    snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", metric_name)
    snake = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", snake)
    return f"aws_rds_{snake.lower()}_{stat.lower()}"


def _dimension_labels(dimensions: list[dict[str, str]]) -> dict[str, str]:
    return {d["Name"].lower(): d["Value"] for d in dimensions if "Name" in d and "Value" in d}


def format_mimir_query_range(cw_fixture: dict[str, Any]) -> dict[str, Any]:
    result_series: list[dict[str, Any]] = []

    for entry in cw_fixture.get("metric_data_results", []):
        name = _metric_name(entry.get("metric_name", "unknown"), entry.get("stat", "average"))
        labels: dict[str, str] = {"__name__": name}
        labels.update(_dimension_labels(entry.get("dimensions", [])))

        timestamps: list[str] = entry.get("timestamps", [])
        values: list[float] = entry.get("values", [])

        prom_values = [[_iso_to_unix(ts), str(v)] for ts, v in zip(timestamps, values)]

        result_series.append({"metric": labels, "values": prom_values})

    return {
        "status": "success",
        "data": {
            "resultType": "matrix",
            "result": result_series,
        },
    }


def format_loki_query_range(rds_events_fixture: dict[str, Any]) -> dict[str, Any]:
    stream_map: dict[tuple[str, str], list[list[str]]] = {}

    for event in rds_events_fixture.get("events", []):
        key = (event.get("source_type", ""), event.get("source_identifier", ""))
        ns_ts = _iso_to_unix_ns(event["date"])
        line = event.get("message", "")
        stream_map.setdefault(key, []).append([ns_ts, line])

    loki_result: list[dict[str, Any]] = []
    for (source_type, source_identifier), log_lines in stream_map.items():
        log_lines.sort(key=lambda x: x[0])
        loki_result.append(
            {
                "stream": {
                    "source_type": source_type,
                    "source_identifier": source_identifier,
                },
                "values": log_lines,
            }
        )

    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": loki_result,
        },
    }


def format_ruler_rules(alert_fixture: dict[str, Any]) -> dict[str, Any]:
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
