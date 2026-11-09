"""Tests for SigNoz tools."""

from typing import Any

from integrations.signoz.tools import (
    _metrics_is_available,
    query_signoz_logs,
    query_signoz_metrics,
    query_signoz_traces,
)
from integrations.signoz.tools.query_signoz_logs_tool.tool import _map_query_signoz_logs
from integrations.signoz.tools.query_signoz_metrics_tool.tool import _map_query_signoz_metrics
from integrations.signoz.tools.query_signoz_traces_tool.tool import _map_query_signoz_traces


class _FakeSigNozBackend:
    """Fake backend for synthetic tests."""

    def query_logs(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_logs",
            "available": True,
            "total": 2,
            "logs": [
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "severity": "ERROR",
                    "severity_number": 17,
                    "message": "connection refused",
                    "trace_id": "abc123",
                    "span_id": "def456",
                    "attributes": {"http.method": "GET"},
                    "resources": {"service.name": "api"},
                },
                {
                    "timestamp": "2024-01-01T00:01:00Z",
                    "severity": "INFO",
                    "severity_number": 9,
                    "message": "request completed",
                    "trace_id": "",
                    "span_id": "",
                    "attributes": {},
                    "resources": {"service.name": "api"},
                },
            ],
        }

    def query_metrics(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_metrics",
            "available": True,
            "total": 2,
            "metric_name": kwargs.get("metric_name"),
            "resolved_metric": kwargs.get("metric_name"),
            "aggregation": kwargs.get("aggregation"),
            "metrics": [
                {
                    "interval": "2024-01-01 00:00:00",
                    "value": 42.0,
                    "metric_name": kwargs.get("metric_name"),
                    "service_name": kwargs.get("service") or "",
                },
            ],
        }

    def query_traces(self, **kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_traces",
            "available": True,
            "total": 1,
            "traces": [
                {
                    "timestamp": "2024-01-01T00:00:00Z",
                    "trace_id": "abc123",
                    "span_id": "span1",
                    "name": "GET /api/health",
                    "duration_ms": 150.0,
                    "has_error": True,
                    "status_code": 2,
                    "status_code_string": "Error",
                    "http_method": "GET",
                    "http_url": "/api/health",
                    "kind_string": "Server",
                    "service_name": kwargs.get("service") or "api",
                },
            ],
        }

    def query_trace_summary(self, **_kwargs: Any) -> dict[str, Any]:
        return {
            "source": "signoz_traces",
            "available": True,
            "total_spans": 100,
            "error_spans": 5,
            "error_rate": 0.05,
            "p99_ms": 250.0,
            "p95_ms": 180.0,
            "avg_ms": 120.0,
            "max_ms": 500.0,
        }


class TestQuerySignozLogs:
    def test_available_with_query_api_credentials_only(self) -> None:
        from integrations.signoz.tools import _logs_is_available

        assert (
            _logs_is_available(
                {
                    "signoz": {
                        "url": "http://localhost:8080",
                        "api_key": "test-key",
                        "connection_verified": False,
                    }
                }
            )
            is True
        )

    def test_backend_injection(self) -> None:
        backend = _FakeSigNozBackend()
        result = query_signoz_logs(
            service="api",
            time_range_minutes=60,
            severity="ERROR",
            limit=10,
            signoz_backend=backend,
        )
        assert result["source"] == "signoz_logs"
        assert result["available"] is True
        assert result["total"] == 2
        assert len(result["logs"]) == 2
        assert len(result["error_logs"]) == 1
        assert result["error_logs"][0]["severity"] == "ERROR"

    def test_not_configured_without_backend(self) -> None:
        result = query_signoz_logs(service="api")
        assert result["source"] == "signoz_logs"
        assert result["available"] is False
        assert "not configured" in result.get("error", "").lower()


class TestQuerySignozMetrics:
    def test_backend_injection(self) -> None:
        backend = _FakeSigNozBackend()
        result = query_signoz_metrics(
            metric_name="cpu_usage",
            service="api",
            time_range_minutes=60,
            aggregation="avg",
            limit=10,
            signoz_backend=backend,
        )
        assert result["source"] == "signoz_metrics"
        assert result["available"] is True
        assert result["metric_name"] == "cpu_usage"
        assert len(result["metrics"]) == 1

    def test_not_configured_without_backend(self) -> None:
        result = query_signoz_metrics(metric_name="cpu_usage")
        assert result["source"] == "signoz_metrics"
        assert result["available"] is False
        assert "not configured" in result.get("error", "").lower()

    def test_available_with_metrics_api_credentials_only(self) -> None:
        assert (
            _metrics_is_available(
                {
                    "signoz": {
                        "url": "http://localhost:8080",
                        "api_key": "test-key",
                        "connection_verified": False,
                    }
                }
            )
            is True
        )


class TestQuerySignozTraces:
    def test_available_with_query_api_credentials_only(self) -> None:
        from integrations.signoz.tools import _traces_is_available

        assert (
            _traces_is_available(
                {
                    "signoz": {
                        "url": "http://localhost:8080",
                        "api_key": "test-key",
                        "connection_verified": False,
                    }
                }
            )
            is True
        )

    def test_backend_injection(self) -> None:
        backend = _FakeSigNozBackend()
        result = query_signoz_traces(
            service="api",
            time_range_minutes=60,
            error_only=True,
            limit=10,
            signoz_backend=backend,
        )
        assert result["source"] == "signoz_traces"
        assert result["available"] is True
        assert result["total"] == 1
        assert result["summary"]["error_rate"] == 0.05

    def test_not_configured_without_backend(self) -> None:
        result = query_signoz_traces(service="api")
        assert result["source"] == "signoz_traces"
        assert result["available"] is False
        assert "not configured" in result.get("error", "").lower()


class TestMapQuerySignozLogs:
    def test_records_plain_count_with_error_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_logs(
            evidence,
            {
                "available": True,
                "total": 2,
                "logs": [{"severity": "ERROR"}, {"severity": "INFO"}],
                "error_logs": [{"severity": "ERROR"}],
            },
            {"limit": 50},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "query_signoz_logs"
        assert entries[0]["summary"] == "2 log(s), 1 error(s)"

    def test_qualifies_count_when_saturated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_logs(
            evidence,
            {
                "available": True,
                "total": 50,
                "logs": [{"severity": "INFO"} for _ in range(50)],
                "error_logs": [],
            },
            {"limit": 50},
        )

        assert evidence["catalog_entries"][0]["summary"] == "50+ log(s)"

    def test_qualifies_count_against_config_effective_limit_not_requested_limit(self) -> None:
        """Regression: SigNozConfig.max_results can clamp the query to a lower
        cap than the caller's requested `limit`. The client echoes the real
        `effective_limit` it used back in output — the mapper must qualify
        against that, not the (higher) requested limit, or a config-capped
        result would be reported as if it were exact."""
        evidence: dict[str, Any] = {}

        _map_query_signoz_logs(
            evidence,
            {
                "available": True,
                "total": 20,
                "effective_limit": 20,
                "logs": [{"severity": "INFO"} for _ in range(20)],
                "error_logs": [],
            },
            {"limit": 200},
        )

        assert evidence["catalog_entries"][0]["summary"] == "20+ log(s)"

    def test_records_nothing_when_no_logs(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_logs(evidence, {"available": True, "total": 0, "logs": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_logs(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence


class TestMapQuerySignozMetrics:
    def test_records_entry_with_metric_and_aggregation(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_metrics(
            evidence,
            {
                "available": True,
                "total": 1,
                "resolved_metric": "cpu_usage",
                "aggregation": "avg",
                "metrics": [{"value": 42.0}],
            },
            {"limit": 50},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "query_signoz_metrics"
        assert entries[0]["summary"] == "cpu_usage (avg): 1 data point(s)"

    def test_qualifies_count_against_config_effective_limit_not_requested_limit(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_metrics(
            evidence,
            {
                "available": True,
                "total": 20,
                "effective_limit": 20,
                "resolved_metric": "cpu_usage",
                "aggregation": "avg",
                "metrics": [{"value": 1.0} for _ in range(20)],
            },
            {"limit": 200},
        )

        assert evidence["catalog_entries"][0]["summary"] == "cpu_usage (avg): 20+ data point(s)"

    def test_records_nothing_when_no_metrics(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_metrics(evidence, {"available": True, "total": 0, "metrics": []}, {})

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_metrics(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence


class TestMapQuerySignozTraces:
    def test_records_entry_from_aggregate_summary(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_traces(
            evidence,
            {
                "available": True,
                "total": 1,
                "traces": [{"trace_id": "abc"}],
                "summary": {
                    "available": True,
                    "total_spans": 100,
                    "error_spans": 5,
                    "p99_ms": 250.0,
                },
            },
            {"limit": 50},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "query_signoz_traces"
        assert entries[0]["summary"] == "100 span(s), 5 error(s), p99 250.0ms"

    def test_falls_back_to_trace_count_when_summary_unavailable(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_traces(
            evidence,
            {
                "available": True,
                "total": 1,
                "traces": [{"trace_id": "abc"}],
                "summary": {"available": False, "error": "aggregation failed"},
            },
            {"limit": 50},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 trace(s)"

    def test_qualifies_fallback_count_against_config_effective_limit(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_traces(
            evidence,
            {
                "available": True,
                "total": 20,
                "effective_limit": 20,
                "traces": [{"trace_id": str(i)} for i in range(20)],
                "summary": {"available": False, "error": "aggregation failed"},
            },
            {"limit": 200},
        )

        assert evidence["catalog_entries"][0]["summary"] == "20+ trace(s)"

    def test_records_nothing_when_summary_and_traces_both_empty(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_traces(
            evidence,
            {"available": True, "total": 0, "traces": [], "summary": {"available": False}},
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_signoz_traces(evidence, {"available": False, "error": "not configured"}, {})

        assert "catalog_entries" not in evidence
