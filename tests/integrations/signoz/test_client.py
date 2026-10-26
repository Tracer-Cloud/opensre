"""Unit tests for SigNoz service client."""

from __future__ import annotations

from typing import Any

import httpx

from integrations.signoz import SigNozConfig
from integrations.signoz.client import SigNozClient


class _FakeMetricsResult:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.row_count = len(rows)
        self.first_row = rows[0] if rows else ()

    def named_results(self) -> list[dict[str, Any]]:
        return self._rows


class _FakeHTTPResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _ErrorHTTPResponse:
    def __init__(self, status_code: int, payload: dict[str, Any]) -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)
        self.request = httpx.Request("POST", "http://localhost")

    def raise_for_status(self) -> None:
        raise httpx.HTTPStatusError(
            f"error {self.status_code}",
            request=self.request,
            response=httpx.Response(self.status_code, request=self.request, json=self._payload),
        )

    def json(self) -> dict[str, Any]:
        return self._payload


def test_query_logs_requires_configuration() -> None:
    result = SigNozClient(SigNozConfig()).query_logs()
    assert result["available"] is False
    assert "SIGNOZ_URL" in result.get("error", "")


def test_query_metrics_uses_query_api_when_configured(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(url: str, **kwargs: Any) -> _FakeHTTPResponse:
        captured["url"] = url
        captured["kwargs"] = kwargs
        return _FakeHTTPResponse(
            {
                "status": "success",
                "data": {
                    "type": "time_series",
                    "data": {
                        "results": [
                            {
                                "queryName": "A",
                                "aggregations": [
                                    {
                                        "series": [
                                            {
                                                "labels": [
                                                    {
                                                        "key": {"name": "service.name"},
                                                        "value": "payments",
                                                    }
                                                ],
                                                "values": [
                                                    {
                                                        "timestamp": 1_700_000_000_000,
                                                        "value": 12.34,
                                                    }
                                                ],
                                            }
                                        ]
                                    }
                                ],
                            }
                        ]
                    },
                },
            }
        )

    def _fake_get(_url: str, **_kwargs: Any) -> _FakeHTTPResponse:
        return _FakeHTTPResponse({"status": "success", "data": {"temporality": "cumulative"}})

    monkeypatch.setattr("integrations.signoz.client.httpx.post", _fake_post)
    monkeypatch.setattr("integrations.signoz.client.httpx.get", _fake_get)

    config = SigNozConfig(url="http://localhost:8080", api_key="test-key")
    result = SigNozClient(config).query_metrics(metric_name="cpu_usage", service="payments")

    assert result["available"] is True
    assert result["query_backend"] == "signoz_query_api"
    assert result["resolved_metric"] == "system_cpu_usage"
    assert result["metrics"][0]["service_name"] == "payments"
    assert captured["url"].endswith("/api/v5/query_range")
    headers = captured["kwargs"]["headers"]
    assert headers["SigNoz-Api-Key"] == "test-key"
    assert (
        captured["kwargs"]["json"]["compositeQuery"]["queries"][0]["spec"]["aggregations"][0][
            "temporality"
        ]
        == "cumulative"
    )


def test_query_metrics_resolves_real_temporality_from_metadata(monkeypatch) -> None:
    """A metric queried with the wrong temporality returns zero rows from the
    backend even though the metric has real data -- the query must use
    whatever temporality the metric was actually ingested with."""
    captured: dict[str, Any] = {}

    def _fake_get(_url: str, **kwargs: Any) -> _FakeHTTPResponse:
        assert kwargs["params"]["metricName"] == "payment_errors_total"
        return _FakeHTTPResponse({"status": "success", "data": {"temporality": "delta"}})

    def _fake_post(_url: str, **kwargs: Any) -> _FakeHTTPResponse:
        captured["payload"] = kwargs.get("json")
        return _FakeHTTPResponse(
            {"status": "success", "data": {"type": "time_series", "data": {"results": []}}}
        )

    monkeypatch.setattr("integrations.signoz.client.httpx.get", _fake_get)
    monkeypatch.setattr("integrations.signoz.client.httpx.post", _fake_post)

    config = SigNozConfig(url="http://localhost:8080", api_key="test-key")
    SigNozClient(config).query_metrics(metric_name="payment_errors_total")

    aggregation = captured["payload"]["compositeQuery"]["queries"][0]["spec"]["aggregations"][0]
    assert aggregation["temporality"] == "delta"


def test_query_metrics_falls_back_to_unspecified_when_metadata_lookup_fails(monkeypatch) -> None:
    """A metadata lookup failure (network error, metric not found, etc.) must not
    block the metrics query itself -- fall back to the prior default instead."""
    captured: dict[str, Any] = {}

    def _fake_get(_url: str, **_kwargs: Any) -> _FakeHTTPResponse:
        raise httpx.ConnectError("connection refused")

    def _fake_post(_url: str, **kwargs: Any) -> _FakeHTTPResponse:
        captured["payload"] = kwargs.get("json")
        return _FakeHTTPResponse(
            {"status": "success", "data": {"type": "time_series", "data": {"results": []}}}
        )

    monkeypatch.setattr("integrations.signoz.client.httpx.get", _fake_get)
    monkeypatch.setattr("integrations.signoz.client.httpx.post", _fake_post)

    config = SigNozConfig(url="http://localhost:8080", api_key="test-key")
    result = SigNozClient(config).query_metrics(metric_name="cpu_usage")

    assert result["available"] is True
    aggregation = captured["payload"]["compositeQuery"]["queries"][0]["spec"]["aggregations"][0]
    assert aggregation["temporality"] == "unspecified"


def test_query_metrics_handles_empty_aggregation_series(monkeypatch) -> None:
    def _fake_post(_url: str, **_kwargs: Any) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(
            {
                "status": "success",
                "data": {
                    "type": "time_series",
                    "data": {
                        "results": [
                            {
                                "queryName": "A",
                                "aggregations": None,
                            }
                        ]
                    },
                },
            }
        )

    monkeypatch.setattr("integrations.signoz.client.httpx.post", _fake_post)

    config = SigNozConfig(url="http://localhost:3301", api_key="test-key")
    result = SigNozClient(config).query_metrics(metric_name="cpu_usage")

    assert result["available"] is True
    assert result["total"] == 0


def test_query_metrics_handles_not_found_via_metrics_api(monkeypatch) -> None:
    def _fake_post(_url: str, **_kwargs: Any) -> _ErrorHTTPResponse:
        return _ErrorHTTPResponse(
            404,
            {"status": "error", "error": {"message": "could not find metric"}},
        )

    monkeypatch.setattr("integrations.signoz.client.httpx.post", _fake_post)
    monkeypatch.setattr(
        "integrations.signoz.client.httpx.get",
        lambda *_a, **_kw: _FakeHTTPResponse({"status": "success", "data": {}}),
    )

    config = SigNozConfig(url="http://localhost:3301", api_key="test-key")
    result = SigNozClient(config).query_metrics(metric_name="cpu_usage")

    assert result["available"] is True
    assert result["total"] == 0
    assert result["query_backend"] == "signoz_query_api"
    assert "warning" in result


def test_query_logs_uses_query_api_when_configured(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(_url: str, **kwargs: Any) -> _FakeHTTPResponse:
        captured["payload"] = kwargs.get("json")
        return _FakeHTTPResponse(
            {
                "status": "success",
                "data": {
                    "type": "raw",
                    "data": {
                        "results": [
                            {
                                "queryName": "A",
                                "rows": [
                                    {
                                        "timestamp": "2024-01-01T00:00:00Z",
                                        "data": {
                                            "body": "connection refused",
                                            "severity_text": "ERROR",
                                            "severity_number": 17,
                                            "trace_id": "abc",
                                            "span_id": "def",
                                            "attributes_string": {},
                                            "resources_string": {"service.name": "api"},
                                        },
                                    }
                                ],
                            }
                        ]
                    },
                },
            }
        )

    monkeypatch.setattr("integrations.signoz.client.httpx.post", _fake_post)

    config = SigNozConfig(url="http://localhost:3301", api_key="test-key")
    result = SigNozClient(config).query_logs(service="api", severity="ERROR", limit=5)

    assert result["available"] is True
    assert result["query_backend"] == "signoz_query_api"
    assert result["total"] == 1
    assert result["logs"][0]["message"] == "connection refused"
    payload = captured["payload"]
    assert payload["requestType"] == "raw"
    assert payload["compositeQuery"]["queries"][0]["spec"]["signal"] == "logs"


def test_query_traces_uses_query_api_when_configured(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(_url: str, **kwargs: Any) -> _FakeHTTPResponse:
        captured["payload"] = kwargs.get("json")
        return _FakeHTTPResponse(
            {
                "status": "success",
                "data": {
                    "type": "raw",
                    "data": {
                        "results": [
                            {
                                "queryName": "A",
                                "rows": [
                                    {
                                        "timestamp": "2024-01-01T00:00:00Z",
                                        "data": {
                                            "serviceName": "api",
                                            "name": "GET /health",
                                            "traceID": "trace-1",
                                            "spanID": "span-1",
                                            "durationNano": 150_000_000,
                                            "hasError": True,
                                            "statusCode": 2,
                                            "statusCodeString": "Error",
                                            "httpMethod": "GET",
                                            "httpUrl": "/health",
                                            "kindString": "Server",
                                        },
                                    }
                                ],
                            }
                        ]
                    },
                },
            }
        )

    monkeypatch.setattr("integrations.signoz.client.httpx.post", _fake_post)

    config = SigNozConfig(url="http://localhost:3301", api_key="test-key")
    result = SigNozClient(config).query_traces(service="api", error_only=True, limit=5)

    assert result["available"] is True
    assert result["query_backend"] == "signoz_query_api"
    assert result["traces"][0]["duration_ms"] == 150.0
    assert result["traces"][0]["has_error"] is True
    payload = captured["payload"]
    assert payload["compositeQuery"]["queries"][0]["spec"]["signal"] == "traces"
    assert (
        "hasError = true" in payload["compositeQuery"]["queries"][0]["spec"]["filter"]["expression"]
    )


def test_query_trace_summary_uses_query_api_when_configured(monkeypatch) -> None:
    def _fake_post(_url: str, **kwargs: Any) -> _FakeHTTPResponse:
        return _FakeHTTPResponse(
            {
                "status": "success",
                "data": {
                    "type": "scalar",
                    "data": {
                        "results": [
                            {
                                "columns": [
                                    {"name": "__result_0", "queryName": "A"},
                                    {"name": "__result_0", "queryName": "B"},
                                    {"name": "__result_0", "queryName": "C"},
                                    {"name": "__result_0", "queryName": "D"},
                                    {"name": "__result_0", "queryName": "E"},
                                    {"name": "__result_0", "queryName": "F"},
                                ],
                                "data": [
                                    [100, 5, 250_000_000, 180_000_000, 120_000_000, 500_000_000]
                                ],
                            }
                        ]
                    },
                },
            }
        )

    monkeypatch.setattr("integrations.signoz.client.httpx.post", _fake_post)

    config = SigNozConfig(url="http://localhost:3301", api_key="test-key")
    result = SigNozClient(config).query_trace_summary(service="api")

    assert result["available"] is True
    assert result["query_backend"] == "signoz_query_api"
    assert result["total_spans"] == 100
    assert result["error_spans"] == 5
    assert result["p99_ms"] == 250.0
