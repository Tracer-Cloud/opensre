"""Unit tests for the enriched metric summary helper.

Pins the agent-facing fields (`mean`, `p95`, `delta`, `delta_pct`,
`window_minutes`) added so agents can summarize raw Prometheus series
without misreading spikes and baselines.
"""

from __future__ import annotations

from infrastructure.evidence.metric_summary import summarize_prometheus_metrics


def _series(metric_name: str, points: list[tuple[float, float]]) -> dict[str, object]:
    """Build a Prometheus matrix-style series."""
    return {
        "metric": {"__name__": metric_name, "dbinstanceidentifier": "orders-prod"},
        "values": [[ts, str(v)] for ts, v in points],
    }


def test_summary_includes_mean_p95_delta_window_minutes() -> None:
    # 15 minute rising trend, 60s period.
    base = 1_700_000_000.0
    points = [(base + i * 60, float(i)) for i in range(16)]  # values 0..15
    [summary] = summarize_prometheus_metrics([_series("cpu_utilization_average", points)])

    assert summary["first"] == 0.0
    assert summary["latest"] == 15.0
    assert summary["min"] == 0.0
    assert summary["max"] == 15.0
    assert summary["mean"] == 7.5
    # p95 over [0..15] linearly interpolated at rank 14.25 → 14.25
    assert summary["p95"] == 14.25
    assert summary["delta"] == 15.0
    assert summary["delta_pct"].startswith("+")
    assert summary["window_minutes"] == 15.0
    assert summary["datapoint_count"] == 16
    assert "increased" in summary["trend"]


def test_flat_series_reports_zero_delta() -> None:
    base = 1_700_000_000.0
    points = [(base + i * 60, 25.0) for i in range(15)]
    [summary] = summarize_prometheus_metrics([_series("cpu_utilization_average", points)])
    assert summary["delta"] == 0.0
    assert summary["mean"] == 25.0
    assert summary["p95"] == 25.0


def test_single_datapoint_summary_is_well_defined() -> None:
    [summary] = summarize_prometheus_metrics(
        [_series("cpu_utilization_average", [(1_700_000_000.0, 42.0)])]
    )
    assert summary["mean"] == 42.0
    assert summary["p95"] == 42.0
    assert summary["window_minutes"] == 0.0


def test_empty_series_does_not_crash() -> None:
    [summary] = summarize_prometheus_metrics([_series("cpu_utilization_average", [])])
    assert summary["trend"] == "no datapoints"
    # Optional enriched fields should not be present without datapoints.
    assert "mean" not in summary
    assert "p95" not in summary
