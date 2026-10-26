"""Tests for NewRelicMetricsTool (class-based, BaseTool subclass)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from integrations.new_relic.tools.new_relic_alerts_tool.results import parse_incident_rows
from integrations.new_relic.tools.new_relic_metrics_tool.tool import (
    NewRelicMetricsTool,
    _map_query_new_relic_metrics,
)
from integrations.new_relic.tools.new_relic_metrics_tool.validation import (
    apply_default_window_and_limit,
    clamp_timeseries_buckets,
    extract_limit,
    validate_nrql,
)
from tests.tools.conftest import BaseToolContract, mock_agent_state

_FIXTURE_PATH = (
    Path(__file__).resolve().parent.parent / "fixtures" / "new_relic" / "incidents_response.json"
)


def _load_fixture_rows() -> list[dict[str, Any]]:
    payload = json.loads(_FIXTURE_PATH.read_text())
    return payload["data"]["actor"]["account"]["nrql"]["results"]  # type: ignore[no-any-return]


class TestNewRelicMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self) -> NewRelicMetricsTool:
        return NewRelicMetricsTool()


def test_is_available_requires_connection_verified_and_account_id() -> None:
    tool = NewRelicMetricsTool()
    assert (
        tool.is_available({"new_relic": {"connection_verified": True, "account_id": "123"}}) is True
    )
    assert tool.is_available({"new_relic": {"connection_verified": True}}) is False
    assert tool.is_available({"new_relic": {"account_id": "123"}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_credentials() -> None:
    tool = NewRelicMetricsTool()
    sources = mock_agent_state(
        {
            "new_relic": {
                "connection_verified": True,
                "api_key": "NRAK-test",
                "account_id": "123456",
                "base_url": "https://api.newrelic.com",
            }
        }
    )
    params = tool.extract_params(sources)
    assert params["api_key"] == "NRAK-test"
    assert params["account_id"] == "123456"


def test_validate_nrql_rejects_empty_and_non_select() -> None:
    is_valid, error = validate_nrql("")
    assert is_valid is False
    assert error

    is_valid, error = validate_nrql("DROP TABLE foo")
    assert is_valid is False


def test_validate_nrql_rejects_mutation_like_text() -> None:
    is_valid, _error = validate_nrql("mutation { alertsPolicyDelete(id: 1) }")
    assert is_valid is False


def test_validate_nrql_accepts_plain_select() -> None:
    is_valid, error = validate_nrql("SELECT count(*) FROM Transaction")
    assert is_valid is True
    assert error == ""


def test_validate_nrql_accepts_a_forbidden_word_inside_a_string_literal() -> None:
    """A filter value containing 'mutation' is not a smuggled GraphQL mutation."""
    is_valid, error = validate_nrql("SELECT count(*) FROM Transaction WHERE url LIKE '%mutation%'")
    assert is_valid is True, error


def test_apply_default_window_and_limit_injects_when_omitted() -> None:
    nrql = "SELECT count(*) FROM Transaction"
    result = apply_default_window_and_limit(nrql, since_minutes=60, limit=100)
    assert "SINCE 60 minutes ago" in result
    assert "LIMIT 100" in result


def test_apply_default_window_and_limit_preserves_explicit_clauses() -> None:
    nrql = "SELECT count(*) FROM Transaction SINCE 10 minutes ago LIMIT 25"
    result = apply_default_window_and_limit(nrql, since_minutes=60, limit=100)
    assert result == nrql


def test_apply_default_window_and_limit_clamps_oversized_limit() -> None:
    nrql = "SELECT count(*) FROM Transaction LIMIT 999999"
    result = apply_default_window_and_limit(nrql, since_minutes=60, limit=100)
    assert "LIMIT 5000" in result
    assert "LIMIT 999999" not in result


def test_apply_default_window_and_limit_inserts_since_before_an_existing_limit() -> None:
    """SINCE must precede LIMIT in NRQL clause order — appending it after LIMIT
    (as a naive default-injection would) produces a query NerdGraph rejects."""
    nrql = "SELECT count(*) FROM Transaction LIMIT 50"
    result = apply_default_window_and_limit(nrql, since_minutes=60, limit=100)
    assert result.index("SINCE") < result.index("LIMIT")
    assert "LIMIT 50" in result


def test_clamp_timeseries_buckets_widens_a_bucket_over_the_nrql_cap() -> None:
    # Production: "TIMESERIES supports a maximum of 366 buckets. Query has 1008."
    nrql = (
        "SELECT count(*) FROM Transaction SINCE 7 days ago TIMESERIES 10 minutes "
        "WHERE name = 'TIMESERIES 1 minute' LIMIT 100"
    )
    result = clamp_timeseries_buckets(nrql)
    assert result == (
        "SELECT count(*) FROM Transaction SINCE 7 days ago TIMESERIES 28 minutes "
        "WHERE name = 'TIMESERIES 1 minute' LIMIT 100"
    )
    # A bucket that already fits, or an absolute window, is left untouched.
    fits = "SELECT count(*) FROM Transaction SINCE 1 day ago TIMESERIES 5 minutes"
    assert clamp_timeseries_buckets(fits) == fits
    absolute = "SELECT count(*) FROM Transaction SINCE 1700000000000 TIMESERIES 1 minute"
    assert clamp_timeseries_buckets(absolute) == absolute


def test_extract_limit_reads_the_effective_clause() -> None:
    assert extract_limit("SELECT 1 FROM Foo LIMIT 42") == 42
    assert extract_limit("SELECT 1 FROM Foo") is None


def test_validate_nrql_accepts_an_identifier_merely_containing_a_forbidden_word() -> None:
    """An event type like 'MutationAuditEvent' is not a smuggled GraphQL mutation."""
    is_valid, error = validate_nrql("SELECT count(*) FROM MutationAuditEvent SINCE 1 hour ago")
    assert is_valid is True, error


def test_apply_default_window_and_limit_ignores_since_inside_a_string_literal() -> None:
    """A filter value containing 'SINCE' must not be mistaken for a real clause."""
    nrql = "SELECT count(*) FROM Transaction WHERE message LIKE '%SINCE yesterday%'"
    result = apply_default_window_and_limit(nrql, since_minutes=60, limit=100)
    assert result.count("SINCE") == 2
    assert "SINCE 60 minutes ago" in result
    assert "LIMIT 100" in result


def test_apply_default_window_and_limit_ignores_limit_inside_a_string_literal() -> None:
    """A filter value containing 'LIMIT 5' must not be read as the real limit."""
    nrql = "SELECT count(*) FROM Transaction WHERE message LIKE '%LIMIT 5%'"
    result = apply_default_window_and_limit(nrql, since_minutes=60, limit=100)
    assert "LIMIT 100" in result
    assert result.count("LIMIT") == 2


def test_extract_limit_ignores_a_limit_like_string_literal() -> None:
    nrql = "SELECT count(*) FROM Transaction WHERE message LIKE '%LIMIT 5%' LIMIT 42"
    assert extract_limit(nrql) == 42
    assert extract_limit("SELECT count(*) FROM Transaction WHERE message LIKE '%LIMIT 5%'") is None


def test_nrql_query_from_alerts_tool_round_trips_into_metrics_tool() -> None:
    """FR-7: the nrqlQuery an alert incident carries is valid metrics-tool input."""
    rows = _load_fixture_rows()
    incidents, _truncated = parse_incident_rows(rows, requested_limit=100)
    alert_nrql = incidents[0]["nrql_query"]
    assert alert_nrql  # sanity: the fixture carries a real query string

    is_valid, error = validate_nrql(alert_nrql)
    assert is_valid is True, error

    tool = NewRelicMetricsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_metrics.return_value = {
        "success": True,
        "results": [{"average.duration": 1.2}],
    }
    with patch(
        "integrations.new_relic.tools.new_relic_metrics_tool.tool.NewRelicClient",
        return_value=mock_client,
    ):
        result = tool.run(nrql=alert_nrql, api_key="NRAK-test", account_id="123456")

    assert result["available"] is True
    # The effective query passed to the client must still include the
    # original alert NRQL text, with defaults injected on top.
    executed_nrql = mock_client.query_metrics.call_args.kwargs["nrql"]
    assert alert_nrql.split(" SINCE")[0] in executed_nrql


def test_run_returns_unavailable_when_not_configured() -> None:
    tool = NewRelicMetricsTool()
    result = tool.run(nrql="SELECT count(*) FROM Transaction", api_key="", account_id="")
    assert result["available"] is False


def test_run_rejects_invalid_nrql_before_calling_client() -> None:
    tool = NewRelicMetricsTool()
    with patch(
        "integrations.new_relic.tools.new_relic_metrics_tool.tool.NewRelicClient"
    ) as client_cls:
        result = tool.run(nrql="", api_key="NRAK-test", account_id="123456")
    assert result["available"] is False
    client_cls.assert_not_called()


def test_run_happy_path() -> None:
    tool = NewRelicMetricsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_metrics.return_value = {
        "success": True,
        "results": [{"average.duration": 0.4}, {"average.duration": 0.5}],
    }
    with patch(
        "integrations.new_relic.tools.new_relic_metrics_tool.tool.NewRelicClient",
        return_value=mock_client,
    ):
        result = tool.run(
            nrql="SELECT average(duration) FROM Transaction",
            api_key="NRAK-test",
            account_id="123456",
        )
    assert result["available"] is True
    assert result["total"] == 2


def test_run_api_error_is_reported_as_unavailable() -> None:
    tool = NewRelicMetricsTool()
    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_metrics.return_value = {
        "success": False,
        "error": "New Relic API returned HTTP 429.",
        "error_type": "rate_limited",
    }
    with patch(
        "integrations.new_relic.tools.new_relic_metrics_tool.tool.NewRelicClient",
        return_value=mock_client,
    ):
        result = tool.run(
            nrql="SELECT count(*) FROM Transaction",
            api_key="NRAK-test",
            account_id="123456",
        )
    assert result["available"] is False
    assert result["error_type"] == "rate_limited"


class TestMapQueryNewRelicMetrics:
    def test_records_entry_with_row_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_new_relic_metrics(
            evidence,
            {"available": True, "results": [{"x": 1}, {"x": 2}, {"x": 3}], "truncated": False},
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "query_new_relic_metrics"
        assert entries[0]["summary"] == "3 row(s)"

    def test_records_entry_with_truncated_note(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_new_relic_metrics(
            evidence, {"available": True, "results": [{"x": 1}], "truncated": True}, {}
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 row(s) (truncated)"

    def test_records_nothing_when_no_results(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_new_relic_metrics(
            evidence, {"available": True, "results": [], "total": 0, "truncated": False}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_new_relic_metrics(
            evidence, {"available": False, "error": "New Relic integration is not configured."}, {}
        )

        assert "catalog_entries" not in evidence
