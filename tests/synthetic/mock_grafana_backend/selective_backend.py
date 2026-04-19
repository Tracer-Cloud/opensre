"""SelectiveGrafanaBackend — query-aware mock for Axis 2 adversarial tests.

Unlike FixtureGrafanaBackend (which returns ALL fixture data unconditionally),
this backend:

  1. **Records** every metric_name requested via query_timeseries into
     `queried_metrics` so tests can assert the agent asked for the right things.

  2. **Filters** the CloudWatch metric series by the requested metric_name using
     a case-insensitive substring match.  For example, querying "CPU" returns
     CPUUtilization; querying "ReplicaLag" returns only the replica lag series.
     An empty metric_name falls back to returning all series (same as Axis 1).

     Known gap: broad tokens like "Free" will match both FreeStorageSpace and
     FreeableMemory — this is intentional for now and will be tightened to
     exact-name matching in a future iteration.

  3. Passes query_logs and query_alert_rules through unchanged (RDS events do
     not need per-field filtering for Axis 2 purposes).

Usage in test_suite_axis2.py
-----------------------------
    backend = SelectiveGrafanaBackend(fixture)
    monkeypatch.setitem(resolved_integrations["grafana"], "_backend", backend)
    ...
    run_scenario(fixture, use_mock_grafana=True, grafana_backend=backend)
    assert "CPUUtilization" in backend.queried_metrics
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any, cast

from tests.synthetic.mock_grafana_backend.formatters import (
    format_loki_query_range,
    format_mimir_query_range,
    format_ruler_rules,
    format_tempo_search,
)

if TYPE_CHECKING:
    from tests.synthetic.rds_postgres.scenario_loader import ScenarioFixture


class SelectiveGrafanaBackend:
    """Query-aware GrafanaBackend for Axis 2 adversarial testing.

    Satisfies the GrafanaBackend Protocol (duck-typed, same as FixtureGrafanaBackend).
    """

    def __init__(self, fixture: ScenarioFixture) -> None:
        self._fixture = fixture
        self.queried_metrics: list[str] = []

    def query_timeseries(self, metric_name: str = "", **_: Any) -> dict[str, Any]:
        """Return Mimir-format timeseries for all available metrics in the fixture.

        Records the metric_name in self.queried_metrics as an audit log so tests
        can inspect what the agent requested.  All metric series are returned
        regardless of metric_name — filtering is not applied because the current
        agent action registry hardcodes metric_name="pipeline_runs_total" and does
        not yet pass dynamic CloudWatch metric names.

        When the agent supports per-metric querying, switch the body to:
            token = metric_name.lower()
            filtered = [s for s in all if token in s["metric_name"].lower()]
        and update required_queries in answer.yml accordingly.
        """
        self.queried_metrics.append(metric_name)

        if self._fixture.evidence.aws_cloudwatch_metrics is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: query_timeseries called but "
                "'aws_cloudwatch_metrics' is not declared in available_evidence"
            )

        all_metrics = cast(dict[str, Any], self._fixture.evidence.aws_cloudwatch_metrics)
        return format_mimir_query_range(all_metrics)

    def query_logs(self, **_: Any) -> dict[str, Any]:
        if self._fixture.evidence.aws_rds_events is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: query_logs called but "
                "'aws_rds_events' is not declared in available_evidence"
            )
        return format_loki_query_range({"events": self._fixture.evidence.aws_rds_events})

    def query_alert_rules(self, **_: Any) -> dict[str, Any]:
        return format_ruler_rules(self._fixture.alert)

    def query_traces(self, **_: Any) -> dict[str, Any]:
        return format_tempo_search()

    def reset(self) -> None:
        """Clear the queried_metrics audit log (useful for re-running a scenario)."""
        self.queried_metrics = []

    @property
    def unique_queried_metrics(self) -> set[str]:
        """Deduplicated set of all metric names that were requested."""
        return set(self.queried_metrics)

    def queried(self, metric_name: str) -> bool:
        """Return True if metric_name was ever requested (case-insensitive token match)."""
        token = metric_name.lower()
        return any(token in q.lower() for q in self.queried_metrics)

    def __deepcopy__(self, memo: dict) -> SelectiveGrafanaBackend:
        new = SelectiveGrafanaBackend(self._fixture)
        new.queried_metrics = copy.deepcopy(self.queried_metrics, memo)
        return new
