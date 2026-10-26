"""Connect timeouts must not look like empty Grafana success.

alert-rules / annotations previously logged ConnectTimeout then returned
``[]``, so SourceCircuitBreaker saw ``is_error=False`` and never marked
Grafana — the next SessionGoal turn re-paid the same connect timeouts.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
import requests

from core.agent_harness.turns.source_circuit_breaker import (
    SourceCircuitBreaker,
    _is_connectivity_error,
)
from core.llm.types import ToolCall
from core.tool.execution import (
    ToolExecutionRequest,
    execute_tool_calls,
)
from integrations.grafana.base import GrafanaClientBase, _is_transport_failure
from integrations.grafana.config import GrafanaAccountConfig


def _timeout() -> requests.exceptions.ConnectTimeout:
    return requests.exceptions.ConnectTimeout(
        "HTTPConnectionPool(host='172.29.15.185', port=3001): Max retries exceeded "
        "with url: /api/ruler/grafana/api/v1/rules (Caused by ConnectTimeoutError("
        "'Connection to 172.29.15.185 timed out. (connect timeout=10)'))"
    )


def _client() -> GrafanaClientBase:
    return GrafanaClientBase(
        GrafanaAccountConfig(
            account_id="t",
            instance_url="http://172.29.15.185:3001",
            read_token="tok",
        )
    )


def test_connect_timeout_is_transport_failure() -> None:
    assert _is_transport_failure(_timeout()) is True


def test_query_alert_rules_reraises_connect_timeout() -> None:
    client = _client()
    with (
        patch.object(client, "_make_get_request", side_effect=_timeout()),
        pytest.raises(requests.exceptions.ConnectTimeout),
    ):
        client.query_alert_rules()


def test_query_annotations_reraises_connect_timeout() -> None:
    client = _client()
    with (
        patch.object(client, "_make_get_request", side_effect=_timeout()),
        pytest.raises(requests.exceptions.ConnectTimeout),
    ):
        client.query_annotations(from_ts=1, to_ts=2)


def test_discover_datasource_uids_reraises_connect_timeout() -> None:
    client = _client()
    with (
        patch.object(client, "_make_get_request", side_effect=_timeout()),
        pytest.raises(requests.exceptions.ConnectTimeout),
    ):
        client.discover_datasource_uids()


def test_discover_timeout_message_trips_connectivity_breaker() -> None:
    # "datasource" in the message must not veto a client ConnectTimeout.
    msg = (
        "Failed to discover datasource UIDs: HTTPConnectionPool(host='172.29.15.185', "
        "port=3001): Max retries exceeded with url: /api/datasources "
        "(Caused by ConnectTimeoutError('Connection to 172.29.15.185 timed out. "
        "(connect timeout=10)'))"
    )
    assert _is_connectivity_error(msg) is True


def test_transport_failure_is_not_sticky_across_client_builds() -> None:
    """Failed builds must not pin the host dead for later /new or recovery."""
    from integrations.grafana import client as grafana_client

    grafana_client._grafana_client_cache.clear()
    grafana_client._grafana_build_inflight.clear()
    calls = {"n": 0}

    def _boom(self: object) -> dict[str, str]:
        calls["n"] += 1
        raise _timeout()

    with patch.object(grafana_client.GrafanaClient, "discover_datasource_uids", _boom):
        with pytest.raises(requests.exceptions.ConnectTimeout):
            grafana_client.get_grafana_client_from_credentials(
                endpoint="http://172.29.15.185:3001",
                api_key="tok",
                account_id="retry_after_fail",
            )
        with pytest.raises(requests.exceptions.ConnectTimeout):
            grafana_client.get_grafana_client_from_credentials(
                endpoint="http://172.29.15.185:3001",
                api_key="tok",
                account_id="retry_after_fail",
            )

    assert calls["n"] == 2
    assert "creds_retry_after_fail_http://172.29.15.185:3001" not in (
        grafana_client._grafana_client_cache
    )


def test_concurrent_builds_share_one_transport_failure() -> None:
    """Parallel gather tools wait on one in-flight build instead of N timeouts."""
    import threading

    from integrations.grafana import client as grafana_client

    grafana_client._grafana_client_cache.clear()
    grafana_client._grafana_build_inflight.clear()
    calls = {"n": 0}
    started = threading.Event()
    release = threading.Event()

    def _slow_boom(self: object) -> dict[str, str]:
        calls["n"] += 1
        started.set()
        assert release.wait(timeout=2.0)
        raise _timeout()

    with patch.object(grafana_client.GrafanaClient, "discover_datasource_uids", _slow_boom):
        errors: list[BaseException] = []

        def _call() -> None:
            try:
                grafana_client.get_grafana_client_from_credentials(
                    endpoint="http://172.29.15.185:3001",
                    api_key="tok",
                    account_id="shared_inflight",
                )
            except Exception as exc:  # collect for assert
                errors.append(exc)

        t1 = threading.Thread(target=_call)
        t2 = threading.Thread(target=_call)
        t1.start()
        assert started.wait(timeout=2.0)
        t2.start()
        release.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

    assert calls["n"] == 1
    assert len(errors) == 2
    assert all(isinstance(exc, requests.exceptions.ConnectTimeout) for exc in errors)


def test_alert_rules_tool_connect_timeout_marks_grafana_source() -> None:
    """End-to-end: tool exception → after hook → breaker marks source."""
    from integrations.grafana.tools import query_grafana_alert_rules

    tool = query_grafana_alert_rules.__opensre_registered_tool__
    breaker = SourceCircuitBreaker()
    hooks = breaker.hooks()
    assert hooks.before_tool_call is not None

    mock_client = MagicMock()
    mock_client.is_configured = True
    mock_client.query_alert_rules.side_effect = _timeout()

    with patch(
        "integrations.grafana.tools._helpers._resolve_grafana_client",
        return_value=mock_client,
    ):
        results = execute_tool_calls(
            [ToolCall(id="1", name=tool.name, input={"grafana_endpoint": "http://g"})],
            [tool],
            {"grafana": {"endpoint": "http://172.29.15.185:3001", "api_key": "x"}},
            hooks=hooks,
        )

    assert results[0].is_error
    _tools, sources = breaker.snapshot()
    assert "grafana" in sources

    sibling = type("T", (), {"source": "grafana"})()
    blocked = hooks.before_tool_call(
        ToolExecutionRequest(
            tool_call=ToolCall(id="2", name="query_grafana_annotations", input={}),
            tool=sibling,  # type: ignore[arg-type]
            arguments={},
            source="grafana",
            resolved_integrations={},
        )
    )
    assert blocked is not None and blocked.blocked
