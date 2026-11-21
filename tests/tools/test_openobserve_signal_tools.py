"""Contracts and request behavior for OpenObserve metrics, traces, and streams."""

from __future__ import annotations

import base64
from typing import Any
from unittest.mock import MagicMock

import pytest

from integrations.openobserve.tools.openobserve_metrics_tool import (
    _map_query_openobserve_metrics,
    query_openobserve_metrics,
)
from integrations.openobserve.tools.openobserve_streams_tool import (
    _map_list_openobserve_streams,
    list_openobserve_streams,
)
from integrations.openobserve.tools.openobserve_traces_tool import (
    _map_query_openobserve_traces,
    query_openobserve_traces,
)
from tests.tools.conftest import BaseToolContract, MockHttpxResponse

BASE_URL = "https://oo.example.invalid"
POST_PATH = "integrations.openobserve.tools._helpers.httpx.post"
GET_PATH = "integrations.openobserve.tools._helpers.httpx.get"


class TestOpenObserveMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return query_openobserve_metrics.__opensre_registered_tool__


class TestOpenObserveTracesToolContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return query_openobserve_traces.__opensre_registered_tool__


class TestOpenObserveStreamsToolContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return list_openobserve_streams.__opensre_registered_tool__


def test_openobserve_tools_register_from_expected_modules() -> None:
    from tools.registry import get_registered_tools

    registered = {
        tool.name: tool.origin_module
        for tool in get_registered_tools()
        if (tool.origin_module or "").startswith("integrations.openobserve.tools.")
    }

    assert registered == {
        "query_openobserve_logs": "integrations.openobserve.tools.openobserve_logs_tool",
        "query_openobserve_metrics": (
            "integrations.openobserve.tools.openobserve_metrics_tool.tool"
        ),
        "query_openobserve_traces": "integrations.openobserve.tools.openobserve_traces_tool.tool",
        "list_openobserve_streams": "integrations.openobserve.tools.openobserve_streams_tool.tool",
    }


@pytest.mark.parametrize(
    "tool_func",
    [query_openobserve_metrics, query_openobserve_traces, list_openobserve_streams],
    ids=["metrics", "traces", "streams"],
)
def test_new_tools_share_source_availability(tool_func: Any) -> None:
    sources = {
        "openobserve": {
            "connection_verified": True,
            "base_url": BASE_URL,
            "api_token": "token",
        }
    }

    assert tool_func.__opensre_registered_tool__.is_available(sources) is True
    assert tool_func.__opensre_registered_tool__.is_available({}) is False


def test_metrics_extract_params_strips_promql_and_reads_step() -> None:
    params = query_openobserve_metrics.__opensre_registered_tool__.extract_params(
        {"openobserve": {"promql": "  rate(http_requests_total[5m])  ", "step_seconds": "15"}}
    )

    assert params["promql"] == "rate(http_requests_total[5m])"
    assert params["step_seconds"] == 15
    assert params["max_results"] == 100


def test_traces_extract_params_honors_stream_precedence() -> None:
    tool = query_openobserve_traces.__opensre_registered_tool__

    specific = tool.extract_params(
        {"openobserve": {"trace_stream": " traces_specific ", "stream": "generic"}}
    )
    generic = tool.extract_params({"openobserve": {"trace_stream": "   ", "stream": " generic "}})
    default = tool.extract_params({"openobserve": {}})

    assert specific["stream"] == "traces_specific"
    assert generic["stream"] == "generic"
    assert default["stream"] == "traces"


def test_streams_extract_params_uses_shared_defaults() -> None:
    params = list_openobserve_streams.__opensre_registered_tool__.extract_params(
        {"openobserve": {"base_url": f"  {BASE_URL}  ", "max_results": 0}}
    )

    assert params["base_url"] == BASE_URL
    assert params["org"] == "default"
    assert params["max_results"] == 100


def _capture_post(monkeypatch: pytest.MonkeyPatch, response: MockHttpxResponse) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _fake_post(
        url: str, headers: dict[str, str], json: dict[str, Any], timeout: float
    ) -> MockHttpxResponse:
        captured.update(url=url, headers=headers, payload=json, timeout=timeout)
        return response

    monkeypatch.setattr(POST_PATH, _fake_post)
    return captured


def _capture_get(monkeypatch: pytest.MonkeyPatch, response: MockHttpxResponse) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    def _fake_get(
        url: str,
        headers: dict[str, str],
        timeout: float,
        params: dict[str, str] | None = None,
    ) -> MockHttpxResponse:
        captured.update(url=url, headers=headers, timeout=timeout, params=params)
        return response

    monkeypatch.setattr(GET_PATH, _fake_get)
    return captured


# Upstream PrometheusRangeQuery contract and response example:
# https://github.com/openobserve/openobserve/blob/v0.15.0/src/handler/http/request/promql/mod.rs#L226-L303
def test_metrics_documented_query_range_contract_response_and_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    series = [{"metric": {"__name__": "up"}, "values": [[1, "1"]]} for _ in range(3)]
    response = {
        "status": "success",
        "data": {"resultType": "matrix", "result": series},
    }
    captured = _capture_get(monkeypatch, MockHttpxResponse(response))

    result = query_openobserve_metrics(
        base_url=f"{BASE_URL}/",
        org="acme",
        promql="  up  ",
        api_token="token",
        username="user",
        password="pass",
        time_range_minutes=15,
        step_seconds=30,
        limit=50,
        max_results=2,
        timeout_seconds=7,
    )

    assert captured["url"] == f"{BASE_URL}/api/acme/prometheus/api/v1/query_range"
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["timeout"] == 7
    params = captured["params"]
    assert params["query"] == "up"
    assert params["step"] == "30"
    assert "size" not in params
    actual_window_seconds = float(params["end"]) - float(params["start"])
    assert abs(actual_window_seconds - 15 * 60) < 1
    assert result["available"] is True
    assert result["records"] == series[:2]
    assert result["total_returned"] == 2
    assert result["effective_limit"] == 2


def test_metrics_query_range_omits_optional_step(monkeypatch: pytest.MonkeyPatch) -> None:
    response = {"status": "success", "data": {"resultType": "matrix", "result": []}}
    captured = _capture_get(monkeypatch, MockHttpxResponse(response))

    result = query_openobserve_metrics(base_url=BASE_URL, promql="up", api_token="token")

    assert "step" not in captured["params"]
    assert result["available"] is True
    assert result["records"] == []


@pytest.mark.parametrize(
    "kwargs",
    [
        {"base_url": " ", "promql": "up", "api_token": "token"},
        {"base_url": BASE_URL, "promql": "up"},
        {"base_url": BASE_URL, "promql": " ", "api_token": "token"},
    ],
    ids=["missing-url", "missing-auth", "missing-promql"],
)
def test_metrics_validation_does_not_call_http(
    kwargs: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    get = MagicMock()
    monkeypatch.setattr(GET_PATH, get)

    result = query_openobserve_metrics(**kwargs)

    assert result["available"] is False
    assert result["records"] == []
    get.assert_not_called()


def test_traces_request_payload_response_and_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spans = [{"trace_id": "abc", "span_id": str(index)} for index in range(3)]
    captured = _capture_post(
        monkeypatch,
        MockHttpxResponse({"hits": {"hits": [{"_source": span} for span in spans]}}),
    )

    result = query_openobserve_traces(
        base_url=BASE_URL,
        org=" ",
        trace_id=" abc ",
        stream=" traces_prod ",
        username="alice",
        password="secret",
        time_range_minutes=30,
        max_results=2,
    )

    assert captured["url"] == f"{BASE_URL}/api/default/_search?type=traces"
    expected_auth = base64.b64encode(b"alice:secret").decode("ascii")
    assert captured["headers"]["Authorization"] == f"Basic {expected_auth}"
    payload = captured["payload"]
    assert payload["size"] == 2
    assert payload["stream_name"] == "traces_prod"
    assert payload["query"]["sql"] == (
        "SELECT * FROM \"traces_prod\" WHERE trace_id = 'abc' ORDER BY _timestamp DESC"
    )
    expected_window_us = 30 * 60 * 1_000_000
    actual_window_us = payload["query"]["end_time"] - payload["query"]["start_time"]
    assert abs(actual_window_us - expected_window_us) < 1_000_000
    assert result["available"] is True
    assert result["trace_id"] == "abc"
    assert result["records"] == spans[:2]


def test_traces_trace_id_escapes_sql_single_quotes(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_post(monkeypatch, MockHttpxResponse({"hits": []}))
    trace_id = "abc' OR '1'='1"

    result = query_openobserve_traces(base_url=BASE_URL, trace_id=trace_id, api_token="token")

    assert captured["payload"]["query"]["sql"] == (
        "SELECT * FROM \"traces\" WHERE trace_id = 'abc'' OR ''1''=''1' ORDER BY _timestamp DESC"
    )
    assert result["trace_id"] == trace_id


def test_traces_custom_sql_uses_default_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_post(monkeypatch, MockHttpxResponse({"hits": []}))
    sql = "SELECT * FROM \"custom_traces\" WHERE service_name = 'api'"

    result = query_openobserve_traces(base_url=BASE_URL, query=sql, api_token="token")

    assert captured["payload"]["query"]["sql"] == sql
    assert captured["payload"]["stream_name"] == "traces"
    assert result["query"] == sql


@pytest.mark.parametrize("tool_func", [query_openobserve_traces, list_openobserve_streams])
def test_traces_and_streams_validate_url_and_auth(
    tool_func: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    post = MagicMock()
    get = MagicMock()
    monkeypatch.setattr(POST_PATH, post)
    monkeypatch.setattr(GET_PATH, get)

    missing_url = tool_func(base_url=" ", api_token="token")
    missing_auth = tool_func(base_url=BASE_URL)

    assert missing_url["available"] is False
    assert missing_auth["available"] is False
    post.assert_not_called()
    get.assert_not_called()


def test_streams_request_dict_response_and_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_get(
        monkeypatch,
        MockHttpxResponse({"streams": [{"name": "logs"}, {"name": "metrics"}, "traces", 123]}),
    )

    result = list_openobserve_streams(
        base_url=f"{BASE_URL}/",
        org="acme",
        api_token="token",
        limit=50,
        max_results=3,
        timeout_seconds=9,
    )

    assert captured["url"] == f"{BASE_URL}/api/acme/streams"
    assert captured["headers"]["Authorization"] == "Bearer token"
    assert captured["timeout"] == 9
    assert result["available"] is True
    assert result["streams"] == [
        {"name": "logs"},
        {"name": "metrics"},
        {"name": "traces"},
    ]
    assert result["total_returned"] == 3
    assert result["effective_limit"] == 3


def test_streams_accepts_bare_list_and_basic_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _capture_get(monkeypatch, MockHttpxResponse([{"name": "logs"}, "traces", None]))

    result = list_openobserve_streams(base_url=BASE_URL, username="alice", password="secret")

    expected_auth = base64.b64encode(b"alice:secret").decode("ascii")
    assert captured["headers"]["Authorization"] == f"Basic {expected_auth}"
    assert result["streams"] == [{"name": "logs"}, {"name": "traces"}]


def test_metrics_http_error_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_get(
        monkeypatch,
        MockHttpxResponse({}, raise_for_status_error=RuntimeError("metrics failed")),
    )

    result = query_openobserve_metrics(base_url=BASE_URL, promql="up", api_token="token")

    assert result["available"] is False
    assert result["records"] == []
    assert "metrics failed" in result["error"]


def test_traces_http_error_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_post(
        monkeypatch,
        MockHttpxResponse({}, raise_for_status_error=RuntimeError("traces failed")),
    )

    result = query_openobserve_traces(base_url=BASE_URL, api_token="token")

    assert result["available"] is False
    assert result["records"] == []
    assert "traces failed" in result["error"]


def test_streams_http_error_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    _capture_get(
        monkeypatch,
        MockHttpxResponse({}, raise_for_status_error=RuntimeError("streams failed")),
    )

    result = list_openobserve_streams(base_url=BASE_URL, api_token="token")

    assert result["available"] is False
    assert result["streams"] == []
    assert "streams failed" in result["error"]


def test_metrics_evidence_mapper_bounds_promql_summary() -> None:
    evidence: dict[str, Any] = {}

    _map_query_openobserve_metrics(
        evidence,
        {
            "available": True,
            "total_returned": 2,
            "effective_limit": 2,
            "promql": "rate(\r\nhttp_requests_total[5m])",
        },
        {},
    )

    entry = evidence["catalog_entries"][0]
    assert entry["source"] == "query_openobserve_metrics"
    assert entry["label"] == "OpenObserve Metrics"
    assert entry["summary"].startswith("2+ metric series, promql 'rate(")
    assert "\r" not in entry["summary"]
    assert "\n" not in entry["summary"]


def test_traces_evidence_mapper_includes_trace_and_query() -> None:
    evidence: dict[str, Any] = {}

    _map_query_openobserve_traces(
        evidence,
        {
            "available": True,
            "total_returned": 2,
            "effective_limit": 50,
            "trace_id": "abc",
            "query": 'SELECT * FROM "traces"',
        },
        {},
    )

    entry = evidence["catalog_entries"][0]
    assert entry["source"] == "query_openobserve_traces"
    assert entry["summary"] == ("2 span(s), trace_id 'abc', query 'SELECT * FROM \"traces\"'")


def test_streams_evidence_mapper_records_count_and_skips_empty_results() -> None:
    evidence: dict[str, Any] = {}

    _map_list_openobserve_streams(
        evidence,
        {"available": True, "total_returned": 2, "effective_limit": 50},
        {},
    )
    _map_list_openobserve_streams(evidence, {"available": True, "total_returned": 0}, {})

    assert len(evidence["catalog_entries"]) == 1
    entry = evidence["catalog_entries"][0]
    assert entry["source"] == "list_openobserve_streams"
    assert entry["summary"] == "2 stream(s) found"
