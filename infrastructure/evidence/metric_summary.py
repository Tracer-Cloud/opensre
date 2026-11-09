"""Compact summaries for time-series metric evidence."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

_PROM_STAT_SUFFIXES = (
    "_average",
    "_minimum",
    "_maximum",
    "_sum",
    "_sample_count",
    "_samplecount",
)

_AWS_RDS_NAME_OVERRIDES = {
    "bin_log_disk_usage": "BinLogDiskUsage",
    "commit_latency": "CommitLatency",
    "commit_throughput": "CommitThroughput",
    "cpu_utilization": "CPUUtilization",
    "database_connections": "DatabaseConnections",
    "disk_queue_depth": "DiskQueueDepth",
    "free_storage_space": "FreeStorageSpace",
    "freeable_memory": "FreeableMemory",
    "maximum_used_transaction_i_ds": "MaximumUsedTransactionIDs",
    "maximum_used_transaction_ids": "MaximumUsedTransactionIDs",
    "network_receive_throughput": "NetworkReceiveThroughput",
    "network_transmit_throughput": "NetworkTransmitThroughput",
    "read_iops": "ReadIOPS",
    "read_latency": "ReadLatency",
    "read_throughput": "ReadThroughput",
    "replica_lag": "ReplicaLag",
    "swap_usage": "SwapUsage",
    "transaction_logs_generation": "TransactionLogsGeneration",
    "write_iops": "WriteIOPS",
    "write_latency": "WriteLatency",
    "write_throughput": "WriteThroughput",
}

_BYTE_METRIC_TOKENS = (
    "bin_log_disk_usage",
    "free_storage_space",
    "freeable_memory",
    "network_receive_throughput",
    "network_transmit_throughput",
    "read_throughput",
    "storage",
    "memory",
    "disk_usage",
    "transaction_logs_generation",
    "write_throughput",
    "bin_log",
    "swap",
)


@dataclass(frozen=True)
class _MetricStats:
    datapoint_count: int
    first_ts: float
    first_value: float
    latest_ts: float
    latest_value: float
    min_ts: float
    min_value: float
    max_ts: float
    max_value: float
    mean_value: float
    p95_value: float


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolated percentile on a pre-sorted list of floats."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    weight = rank - lo
    return sorted_values[lo] * (1 - weight) + sorted_values[hi] * weight


def summarize_prometheus_metrics(series: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return compact summaries for Prometheus/Mimir matrix series."""
    summaries: list[dict[str, Any]] = []
    for item in series:
        if not isinstance(item, dict):
            continue

        metric = item.get("metric", {})
        if not isinstance(metric, dict):
            metric = {}
        raw_name = str(metric.get("__name__", "unknown")).strip() or "unknown"
        values = _parse_values(item.get("values", []))
        stats = _compute_stats(values)
        labels = {str(k): str(v) for k, v in metric.items() if k != "__name__"}
        display_name = _display_metric_name(raw_name)

        summary = {
            "metric_name": display_name,
            "raw_metric_name": raw_name,
            "labels": labels,
            "datapoint_count": len(values),
            "summary": _build_summary_line(display_name, raw_name, labels, stats),
        }
        if stats:
            window_seconds = stats.latest_ts - stats.first_ts
            summary.update(
                {
                    "first": stats.first_value,
                    "first_timestamp": _format_timestamp(stats.first_ts),
                    "latest": stats.latest_value,
                    "latest_timestamp": _format_timestamp(stats.latest_ts),
                    "min": stats.min_value,
                    "min_timestamp": _format_timestamp(stats.min_ts),
                    "max": stats.max_value,
                    "max_timestamp": _format_timestamp(stats.max_ts),
                    "mean": round(stats.mean_value, 4),
                    "p95": round(stats.p95_value, 4),
                    "peak": stats.max_value,
                    "peak_timestamp": _format_timestamp(stats.max_ts),
                    "trend": _trend(stats.first_value, stats.latest_value),
                    "delta": round(stats.latest_value - stats.first_value, 4),
                    "delta_pct": _change(stats.first_value, stats.latest_value),
                    "peak_to_latest_change": _change(stats.max_value, stats.latest_value),
                    "window_minutes": (
                        round(window_seconds / 60.0, 1) if window_seconds > 0 else 0.0
                    ),
                }
            )
        else:
            summary["trend"] = "no datapoints"
        summaries.append(summary)
    return summaries


def _compute_stats(values: list[tuple[float, float]]) -> _MetricStats | None:
    if not values:
        return None

    first_ts, first_value = values[0]
    latest_ts, latest_value = values[-1]
    min_ts, min_value = first_ts, first_value
    max_ts, max_value = first_ts, first_value
    for timestamp, value in values[1:]:
        if value < min_value:
            min_ts, min_value = timestamp, value
        if value > max_value:
            max_ts, max_value = timestamp, value

    raw_values = [v for _, v in values]
    mean_value = sum(raw_values) / len(raw_values)
    p95_value = _percentile(sorted(raw_values), 95.0)

    return _MetricStats(
        datapoint_count=len(values),
        first_ts=first_ts,
        first_value=first_value,
        latest_ts=latest_ts,
        latest_value=latest_value,
        min_ts=min_ts,
        min_value=min_value,
        max_ts=max_ts,
        max_value=max_value,
        mean_value=mean_value,
        p95_value=p95_value,
    )


def _parse_values(raw_values: Any) -> list[tuple[float, float]]:
    parsed: list[tuple[float, float]] = []
    if not isinstance(raw_values, list):
        return parsed
    for item in raw_values:
        if not isinstance(item, (list, tuple)) or len(item) < 2:
            continue
        try:
            timestamp = float(item[0])
            value = float(item[1])
        except (TypeError, ValueError):
            continue
        parsed.append((timestamp, value))
    return parsed


def _display_metric_name(raw_name: str) -> str:
    base = raw_name
    if base.startswith("aws_rds_"):
        base = base.removeprefix("aws_rds_")
        for suffix in _PROM_STAT_SUFFIXES:
            if base.endswith(suffix):
                base = base[: -len(suffix)]
                break
        return _AWS_RDS_NAME_OVERRIDES.get(base, _title_from_snake(base))
    return raw_name


def _title_from_snake(value: str) -> str:
    return "".join(part.capitalize() for part in value.split("_") if part)


def _build_summary_line(
    display_name: str,
    raw_name: str,
    labels: dict[str, str],
    stats: _MetricStats | None,
) -> str:
    label_text = _format_labels(labels)
    if not stats:
        return f"{display_name}{label_text}: no datapoints"

    value_context = _value_context(raw_name, display_name)
    return (
        f"{display_name}{label_text}: datapoints={stats.datapoint_count}, "
        f"first={_format_value(stats.first_value, value_context)} at "
        f"{_format_timestamp(stats.first_ts)}, "
        f"latest={_format_value(stats.latest_value, value_context)} at "
        f"{_format_timestamp(stats.latest_ts)}, "
        f"min={_format_value(stats.min_value, value_context)} at "
        f"{_format_timestamp(stats.min_ts)}, "
        f"max/peak={_format_value(stats.max_value, value_context)} at "
        f"{_format_timestamp(stats.max_ts)}, "
        f"trend={_trend(stats.first_value, stats.latest_value)}, "
        f"peak_to_latest={_change(stats.max_value, stats.latest_value)}"
    )


def _format_labels(labels: dict[str, str]) -> str:
    if not labels:
        return ""
    label_text = ", ".join(f"{key}={value}" for key, value in sorted(labels.items()))
    return f" ({label_text})"


def _value_context(raw_name: str, display_name: str) -> str:
    name = f"{raw_name} {display_name}".lower()
    if any(token in name for token in _BYTE_METRIC_TOKENS):
        return "bytes"
    return "number"


def _format_value(value: float, value_context: str) -> str:
    if value_context == "bytes":
        return _format_bytes(value)
    if value.is_integer():
        return str(int(value))
    return f"{value:.4g}"


def _format_bytes(value: float) -> str:
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    amount = abs(value)
    unit_index = 0
    while amount >= 1024 and unit_index < len(units) - 1:
        amount /= 1024
        unit_index += 1
    signed = -amount if value < 0 else amount
    if unit_index == 0:
        return f"{signed:.0f} {units[unit_index]}"
    return f"{signed:.2f} {units[unit_index]}"


def _format_timestamp(value: float) -> str:
    try:
        return datetime.fromtimestamp(value, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OverflowError, OSError, ValueError):
        return str(value)


def _trend(first: float, latest: float) -> str:
    change = _change(first, latest)
    if latest > first:
        return f"increased {change}"
    if latest < first:
        return f"decreased {change}"
    return "flat"


def _change(start: float, end: float) -> str:
    delta = end - start
    if start == 0:
        if end == 0:
            return "0"
        return f"{_format_signed_number(delta)} from zero"
    pct = abs(delta / start) * 100
    return f"{pct:.1f}% ({_format_signed_number(delta)})"


def _format_signed_number(value: float) -> str:
    if value.is_integer():
        return f"{int(value):+d}"
    return f"{value:+.4g}"
