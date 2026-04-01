"""GrafanaBackend Protocol and FixtureGrafanaBackend for synthetic testing.

The Protocol defines the minimal surface the RDS agent uses to query observability
data.  FixtureGrafanaBackend satisfies it by serving scenario fixture data formatted
as Grafana wire-format responses — zero HTTP calls required.

Usage in run_suite.py
---------------------
    state["grafana_backend"] = FixtureGrafanaBackend(fixture)

The production resolver in grafana_actions._resolve_grafana_backend reads this key
first, falling back to real HTTP calls when absent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, cast, runtime_checkable

from tests.synthetic.mock_grafana_backend.formatters import (
    format_loki_query_range,
    format_mimir_query_range,
    format_ruler_rules,
)

if TYPE_CHECKING:
    from tests.synthetic.rds_postgres.scenario_loader import ScenarioFixture


@runtime_checkable
class GrafanaBackend(Protocol):
    """Minimal observability interface used by the RDS investigation agent.

    Three methods — one per evidence pillar:
        query_timeseries  → Mimir/Prometheus matrix response
        query_logs        → Loki streams response
        query_alert_rules → Grafana Ruler rules response
    """

    def query_timeseries(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        """Return a Mimir-compatible query_range response."""
        pass

    def query_logs(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        """Return a Loki-compatible query_range response."""
        pass

    def query_alert_rules(self, **kwargs: Any) -> dict[str, Any]:
        """Return a Grafana Ruler /api/v1/rules response."""
        pass


class FixtureGrafanaBackend:
    """GrafanaBackend implementation backed by a ScenarioFixture.

    All three methods delegate to the pure formatter functions, converting
    AWS-faithful fixture data into the Grafana wire format the agent expects.
    No HTTP calls, no external dependencies.
    """

    def __init__(self, fixture: ScenarioFixture) -> None:
        self._fixture = fixture

    def query_timeseries(self, **_: Any) -> dict[str, Any]:
        if self._fixture.evidence.aws_cloudwatch_metrics is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: query_timeseries called but "
                "'aws_cloudwatch_metrics' is not declared in available_evidence"
            )
        metrics = cast(dict[str, Any], self._fixture.evidence.aws_cloudwatch_metrics)
        return format_mimir_query_range(metrics)

    def query_logs(self, **_: Any) -> dict[str, Any]:
        if self._fixture.evidence.aws_rds_events is None:
            raise ValueError(
                f"{self._fixture.scenario_id}: query_logs called but "
                "'aws_rds_events' is not declared in available_evidence"
            )
        return format_loki_query_range({"events": self._fixture.evidence.aws_rds_events})

    def query_alert_rules(self, **_: Any) -> dict[str, Any]:
        return format_ruler_rules(self._fixture.alert)
