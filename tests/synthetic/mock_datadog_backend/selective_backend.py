"""SelectiveDatadogBackend — query-aware mock for Axis 2 adversarial tests.

Records every query string and filter passed to ``query_logs`` and
``query_monitors`` so tests can assert the agent searched for the right things.
Returns the full fixture data on every call; filtering is deferred to a later
iteration.

Usage in test_suite_axis2.py
-----------------------------
    backend = SelectiveDatadogBackend(fixture)
    final_state, score = run_scenario(fixture, use_mock_backends=True, datadog_backend=backend)
    assert any("pipeline_error" in q.lower() for q in backend.queried_log_queries)
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from tests.synthetic.mock_datadog_backend.backend import FixtureDatadogBackend

if TYPE_CHECKING:
    from tests.synthetic.eks.scenario_loader import K8sScenarioFixture


class SelectiveDatadogBackend(FixtureDatadogBackend):
    """Query-aware DatadogBackend for Axis 2 adversarial testing."""

    def __init__(self, fixture: K8sScenarioFixture) -> None:
        super().__init__(fixture)
        self.queried_log_queries: list[str] = []
        self.queried_monitor_filters: list[str] = []
        self.queried_tools: list[str] = []

    def query_logs(self, query: str = "", **kwargs: Any) -> dict[str, Any]:
        self.queried_tools.append("query_logs")
        self.queried_log_queries.append(query)
        return super().query_logs(query=query, **kwargs)

    def query_monitors(self, query: str | None = None, **kwargs: Any) -> dict[str, Any]:
        self.queried_tools.append("query_monitors")
        self.queried_monitor_filters.append(query or "")
        return super().query_monitors(query=query, **kwargs)

    def reset(self) -> None:
        """Clear the audit log (useful for re-running a scenario)."""
        self.queried_log_queries = []
        self.queried_monitor_filters = []
        self.queried_tools = []

    @property
    def unique_queried_tools(self) -> set[str]:
        """Deduplicated set of all tool methods that were invoked."""
        return set(self.queried_tools)

    def queried(self, tool_name: str) -> bool:
        """Return True if ``tool_name`` was ever invoked (exact match)."""
        return tool_name in self.queried_tools

    def __deepcopy__(self, memo: dict) -> SelectiveDatadogBackend:
        new = SelectiveDatadogBackend(self._fixture)
        new.queried_log_queries = copy.deepcopy(self.queried_log_queries, memo)
        new.queried_monitor_filters = copy.deepcopy(self.queried_monitor_filters, memo)
        new.queried_tools = copy.deepcopy(self.queried_tools, memo)
        return new
