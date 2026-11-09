"""Tests for the New Relic NerdGraph client.

Pins the real failure modes from plan.md §2/§4: GraphQL's "HTTP 200 with a
populated errors field" failure mode, the mandatory HTTP-then-errors-then-data
checking order, 401 vs. an unreachable account being distinguishable, and the
5s NRQL-via-API timeout never being confused with an empty-but-successful
result (NFR-7).
"""

from __future__ import annotations

from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from integrations.new_relic.client import NewRelicClient
from integrations.new_relic.config import NewRelicIntegrationConfig

# Obviously-fake credentials and identifiers — never real account data.
_FAKE_API_KEY = "NRAK-test-fake-not-a-real-key-00"
_FAKE_ACCOUNT_ID = "1234567"


@pytest.fixture
def config() -> NewRelicIntegrationConfig:
    return NewRelicIntegrationConfig(api_key=_FAKE_API_KEY, account_id=_FAKE_ACCOUNT_ID)


@pytest.fixture
def client(config: NewRelicIntegrationConfig) -> NewRelicClient:
    return NewRelicClient(config)


def _response(status_code: HTTPStatus, payload: dict[str, Any]) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = payload
    return response


def _mock_transport(client: NewRelicClient, response: MagicMock) -> MagicMock:
    mock_http = MagicMock()
    mock_http.post.return_value = response
    client._client = mock_http
    return mock_http


def test_run_nrql_success_with_realistic_incident_fixture(client: NewRelicClient) -> None:
    """A realistic NrAiIncident payload round-trips through run_nrql untouched.

    Pairing open/close rows by ``incidentId``, HTML-entity decoding, and
    threshold parsing are the alerts tool's job (Phase 3) — this pins only
    the transport: the raw rows come back exactly as NerdGraph sent them.
    """
    rows = [
        {
            "incidentId": 1001,
            "event": "open",
            "priority": "critical",
            "title": "checkout-latency response time &gt;= 3.0",
            "conditionName": "checkout-latency",
            "policyName": "checkout-service",
            "nrqlQuery": "SELECT average(duration) FROM Transaction",
            "threshold": "3.0",
            "operator": "&gt;=",
            "runbookUrl": "https://runbooks.example.com/checkout-latency",
            "entity.name": "checkout-service",
            "entity.guid": "FAKE-GUID-1",
            "muted": False,
            "openTime": 1710000000000,
            "closeTime": None,
        },
        {
            "incidentId": 1001,
            "event": "close",
            "priority": "critical",
            "title": "checkout-latency response time &gt;= 3.0",
            "conditionName": "checkout-latency",
            "policyName": "checkout-service",
            "nrqlQuery": "SELECT average(duration) FROM Transaction",
            "threshold": "3.0",
            "operator": "&gt;=",
            "runbookUrl": "https://runbooks.example.com/checkout-latency",
            "entity.name": "checkout-service",
            "entity.guid": "FAKE-GUID-1",
            "muted": False,
            "openTime": 1710000000000,
            "closeTime": 1710000300000,
        },
    ]
    payload = {"data": {"actor": {"account": {"nrql": {"results": rows}}}}}
    _mock_transport(client, _response(HTTPStatus.OK, payload))

    result = client.run_nrql("SELECT ... FROM NrAiIncident")

    assert result["success"] is True
    assert result["results"] == rows


def test_run_nrql_empty_results_is_not_an_error(client: NewRelicClient) -> None:
    payload: dict[str, Any] = {"data": {"actor": {"account": {"nrql": {"results": []}}}}}
    _mock_transport(client, _response(HTTPStatus.OK, payload))

    result = client.run_nrql("SELECT count(*) FROM NrAiIncident")

    assert result["success"] is True
    assert result["results"] == []
    assert "error_type" not in result


def test_run_nrql_graphql_200_with_errors_is_structured_error(client: NewRelicClient) -> None:
    """NerdGraph's #1 failure mode: HTTP 200 with a populated ``errors`` field."""
    nrql = "SELECT count(*) FROM NrAiIncident"
    payload = {"errors": [{"message": "Field 'nrql' doesn't exist on type 'Account'"}]}
    _mock_transport(client, _response(HTTPStatus.OK, payload))

    result = client.run_nrql(nrql)

    assert result["success"] is False
    assert result["error_type"] == "graphql_error"
    # The vendor message is sanitized: our own query text is never echoed back,
    # since it may embed account-specific literals.
    assert nrql not in result["error"]


def test_run_nrql_401_is_distinct_from_unreachable_account(client: NewRelicClient) -> None:
    _mock_transport(client, _response(HTTPStatus.UNAUTHORIZED, {}))
    invalid_key_result = client.run_nrql("SELECT count(*) FROM NrAiIncident")

    account_null_payload = {"data": {"actor": {"account": None}}}
    _mock_transport(client, _response(HTTPStatus.OK, account_null_payload))
    unreachable_account_result = client.run_nrql("SELECT count(*) FROM NrAiIncident")

    assert invalid_key_result["success"] is False
    assert invalid_key_result["error_type"] == "auth_error"
    assert unreachable_account_result["success"] is False
    assert unreachable_account_result["error_type"] == "account_unreachable"
    assert invalid_key_result["error"] != unreachable_account_result["error"]


@pytest.mark.parametrize(
    ("status_code", "expected_error_type"),
    [
        (HTTPStatus.TOO_MANY_REQUESTS, "rate_limited"),
        (HTTPStatus.INTERNAL_SERVER_ERROR, "server_error"),
        (HTTPStatus.SERVICE_UNAVAILABLE, "server_error"),
    ],
)
def test_run_nrql_rate_limit_and_server_errors_are_structured(
    client: NewRelicClient, status_code: HTTPStatus, expected_error_type: str
) -> None:
    _mock_transport(client, _response(status_code, {}))

    result = client.run_nrql("SELECT count(*) FROM NrAiIncident")

    assert result["success"] is False
    assert result["error_type"] == expected_error_type
    # NFR-1: no vendor exception detail or stack trace ever reaches the caller.
    assert "Traceback" not in result["error"]


def test_run_nrql_timeout_is_not_confused_with_empty_result(client: NewRelicClient) -> None:
    """NFR-7: the 5s NRQL-via-API timeout must never read as "no data"."""
    mock_http = MagicMock()
    mock_http.post.side_effect = httpx.ReadTimeout("timed out")
    client._client = mock_http

    result = client.run_nrql("SELECT count(*) FROM Transaction SINCE 1 day ago")

    assert result["success"] is False
    assert result["error_type"] == "timeout"
    assert result["error_type"] != "empty"


def test_query_incidents_builds_the_expected_nrql(client: NewRelicClient) -> None:
    mock_http = _mock_transport(
        client,
        _response(HTTPStatus.OK, {"data": {"actor": {"account": {"nrql": {"results": []}}}}}),
    )

    client.query_incidents(
        since_minutes=30, priority="critical", entity_name="checkout-service", limit=10
    )

    sent_nrql = mock_http.post.call_args.kwargs["json"]["variables"]["nrql"]
    assert "FROM NrAiIncident" in sent_nrql
    assert "priority = 'critical'" in sent_nrql
    assert "entity.name = 'checkout-service'" in sent_nrql
    assert "SINCE 30 minutes ago" in sent_nrql
    assert "LIMIT 10" in sent_nrql


def test_query_incidents_orders_newest_first_so_a_cutoff_never_drops_the_close_row(
    client: NewRelicClient,
) -> None:
    """A resolved incident's close row is always chronologically >= its open
    row. Explicit newest-first ordering guarantees a LIMIT cutoff can only
    ever drop the (redundant) open row, never the close row that alone
    already carries both openTime and closeTime — never the reverse, which
    would misreport a resolved incident as still open."""
    mock_http = _mock_transport(
        client,
        _response(HTTPStatus.OK, {"data": {"actor": {"account": {"nrql": {"results": []}}}}}),
    )

    client.query_incidents(since_minutes=30, limit=10)

    sent_nrql = mock_http.post.call_args.kwargs["json"]["variables"]["nrql"]
    assert sent_nrql.index("ORDER BY timestamp DESC") < sent_nrql.index("LIMIT 10")


def test_query_incidents_reports_the_effective_limit_after_clamping(
    client: NewRelicClient,
) -> None:
    """Callers need the *executed* limit to detect truncation correctly.

    A request above the vendor's 5,000-row ceiling gets clamped before the
    query runs — the result must carry that clamped value, not the caller's
    original ask, or a full capped response reads as untruncated.
    """
    mock_http = _mock_transport(
        client,
        _response(HTTPStatus.OK, {"data": {"actor": {"account": {"nrql": {"results": []}}}}}),
    )

    result = client.query_incidents(since_minutes=30, limit=999_999)

    sent_nrql = mock_http.post.call_args.kwargs["json"]["variables"]["nrql"]
    assert "LIMIT 5000" in sent_nrql
    assert result["effective_limit"] == 5000


def test_probe_access_distinguishes_invalid_key_from_unreachable_account(
    client: NewRelicClient,
) -> None:
    _mock_transport(client, _response(HTTPStatus.UNAUTHORIZED, {}))
    invalid_key = client.probe_access()

    account_null_payload = {"data": {"actor": {"user": {"name": "sre-bot"}, "account": None}}}
    _mock_transport(client, _response(HTTPStatus.OK, account_null_payload))
    unreachable_account = client.probe_access()

    assert invalid_key.status == "failed"
    assert unreachable_account.status == "failed"
    assert invalid_key.detail != unreachable_account.detail


def test_probe_access_success(client: NewRelicClient) -> None:
    payload = {"data": {"actor": {"user": {"name": "sre-bot"}, "account": {"name": "Fake Co"}}}}
    _mock_transport(client, _response(HTTPStatus.OK, payload))

    result = client.probe_access()

    assert result.status == "passed"


def test_probe_access_missing_credentials() -> None:
    missing_config = NewRelicIntegrationConfig(api_key="", account_id="")
    missing_client = NewRelicClient(missing_config)

    result = missing_client.probe_access()

    assert result.status == "missing"
