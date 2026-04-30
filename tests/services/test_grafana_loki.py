"""Direct unit tests for app.services.grafana.loki.LokiMixin.

Exercise the Loki query path with a fake mixin host so we never hit a real
Grafana datasource. Covers the happy path (stream flattening, metadata
propagation), the not-configured short-circuit, exception handling with and
without a wrapped HTTP response, and the empty-result envelope.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.grafana.loki import LokiMixin

# ---------------------------------------------------------------------------
# Test host
# ---------------------------------------------------------------------------


class _FakeLokiHost(LokiMixin):
    """Minimal stand-in for GrafanaClientBase that satisfies LokiMixin's needs.

    LokiMixin reads ``is_configured``, ``account_id``, and
    ``loki_datasource_uid`` and calls ``_build_datasource_url`` and
    ``_make_request``. Everything else on the real base is irrelevant here.
    """

    def __init__(
        self,
        *,
        is_configured: bool = True,
        account_id: str = "acct-1",
        loki_datasource_uid: str = "loki-uid",
        instance_url: str = "https://grafana.example.com",
    ) -> None:
        self.is_configured = is_configured  # type: ignore[assignment]
        self.account_id = account_id
        self.loki_datasource_uid = loki_datasource_uid
        self.instance_url = instance_url
        self.last_url: str | None = None
        self.last_params: dict[str, str] | None = None
        self.make_request_mock: MagicMock = MagicMock()

    def _build_datasource_url(self, datasource_uid: str, path: str) -> str:
        return f"{self.instance_url}/api/datasources/proxy/uid/{datasource_uid}{path}"

    def _make_request(
        self,
        url: str,
        params: dict[str, str] | None = None,
        timeout: int = 10,
    ) -> dict[str, Any]:
        self.last_url = url
        self.last_params = params
        return self.make_request_mock(url, params=params, timeout=timeout)


# ---------------------------------------------------------------------------
# Fixture data
# ---------------------------------------------------------------------------


_QUERY = '{service_name="lambda-mock-dag"}'


def _two_stream_response() -> dict[str, Any]:
    return {
        "status": "success",
        "data": {
            "resultType": "streams",
            "result": [
                {
                    "stream": {"service_name": "lambda-mock-dag", "level": "error"},
                    "values": [
                        ["1700000000000000000", "boom"],
                        ["1700000000000000001", "crash"],
                    ],
                },
                {
                    "stream": {"service_name": "lambda-mock-dag", "level": "info"},
                    "values": [["1700000000000000002", "started"]],
                },
            ],
        },
    }


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestQueryLokiSuccess:
    """Configured client returns a flattened, well-formed envelope."""

    def test_flattens_streams_into_log_rows(self) -> None:
        host = _FakeLokiHost()
        host.make_request_mock.return_value = _two_stream_response()

        result = host.query_loki(_QUERY)

        assert result["success"] is True
        assert result["query"] == _QUERY
        assert result["account_id"] == "acct-1"
        assert result["total_streams"] == 2
        assert result["total_logs"] == 3
        assert len(result["logs"]) == 3

    def test_each_log_row_carries_timestamp_message_and_labels(self) -> None:
        host = _FakeLokiHost()
        host.make_request_mock.return_value = _two_stream_response()

        result = host.query_loki(_QUERY)
        first = result["logs"][0]

        assert first == {
            "timestamp": "1700000000000000000",
            "message": "boom",
            "labels": {"service_name": "lambda-mock-dag", "level": "error"},
        }
        assert result["logs"][2]["labels"]["level"] == "info"
        assert result["logs"][2]["message"] == "started"

    def test_uses_loki_datasource_url_and_passes_query_params(self) -> None:
        host = _FakeLokiHost(loki_datasource_uid="my-loki")
        host.make_request_mock.return_value = _two_stream_response()

        fake_now_s = 1_700_000_000.0
        with patch("app.services.grafana.loki.time.time", return_value=fake_now_s):
            host.query_loki(_QUERY, time_range_minutes=5, limit=42)

        assert host.last_url is not None
        assert "/api/datasources/proxy/uid/my-loki/loki/api/v1/query_range" in host.last_url
        assert host.last_params is not None
        assert host.last_params["query"] == _QUERY
        assert host.last_params["limit"] == "42"
        expected_end = int(fake_now_s * 1e9)
        expected_start = expected_end - (5 * 60 * int(1e9))
        assert host.last_params["start"] == str(expected_start)
        assert host.last_params["end"] == str(expected_end)


# ---------------------------------------------------------------------------
# Not configured short-circuit
# ---------------------------------------------------------------------------


class TestQueryLokiNotConfigured:
    """When is_configured is False we never reach the HTTP layer."""

    def test_returns_not_configured_envelope(self) -> None:
        host = _FakeLokiHost(is_configured=False, account_id="missing-acct")

        result = host.query_loki(_QUERY)

        assert result == {
            "success": False,
            "error": "Grafana client not configured for account 'missing-acct'",
            "logs": [],
        }

    def test_does_not_invoke_make_request(self) -> None:
        host = _FakeLokiHost(is_configured=False)

        host.query_loki(_QUERY)

        host.make_request_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Exception handling
# ---------------------------------------------------------------------------


class TestQueryLokiExceptions:
    """Exceptions surface as a stable failure envelope."""

    def test_plain_exception_returns_str_error_and_empty_response(self) -> None:
        host = _FakeLokiHost()
        host.make_request_mock.side_effect = RuntimeError("connection refused")

        result = host.query_loki(_QUERY)

        assert result == {
            "success": False,
            "error": "connection refused",
            "response": "",
            "logs": [],
        }

    def test_exception_with_response_includes_status_and_truncated_text(self) -> None:
        host = _FakeLokiHost()

        long_text = "x" * 500
        response = MagicMock(status_code=502, text=long_text)
        err = RuntimeError("bad gateway")
        err.response = response  # type: ignore[attr-defined]
        host.make_request_mock.side_effect = err

        result = host.query_loki(_QUERY)

        assert result["success"] is False
        assert result["error"] == "Loki query failed: 502"
        assert result["response"] == long_text[:300]
        assert len(result["response"]) == 300
        assert result["logs"] == []

    def test_exception_with_none_response_falls_back_to_str_error(self) -> None:
        host = _FakeLokiHost()
        err = RuntimeError("transport error")
        err.response = None  # type: ignore[attr-defined]
        host.make_request_mock.side_effect = err

        result = host.query_loki(_QUERY)

        assert result["success"] is False
        assert result["error"] == "transport error"
        assert result["response"] == ""


# ---------------------------------------------------------------------------
# Empty-result envelope stability
# ---------------------------------------------------------------------------


class TestQueryLokiEmptyResult:
    """No streams returned should still yield a valid success envelope."""

    @pytest.mark.parametrize(
        "payload",
        [
            {"data": {"result": []}},
            {"data": {}},
            {},
        ],
    )
    def test_zero_streams_yields_empty_logs_but_success(self, payload: dict) -> None:
        host = _FakeLokiHost()
        host.make_request_mock.return_value = payload

        result = host.query_loki(_QUERY)

        assert result["success"] is True
        assert result["logs"] == []
        assert result["total_streams"] == 0
        assert result["total_logs"] == 0
        assert result["query"] == _QUERY
        assert result["account_id"] == "acct-1"
