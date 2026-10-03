"""Dedicated unit tests for OpenSearchAnalyticsTool.

Covers contract metadata, source-availability gating, parameter extraction,
client config normalization, bounded result limits, log filtering/normalization
on the tool side (non-dict items dropped), propagation of client errors, and
setup guidance when only re-running setup fixes the cluster's answer.
"""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import Any

import httpx
import pytest

from config.constants.opensearch import (
    OPENSEARCH_INTEGRATION_SETUP_CLI,
    OPENSEARCH_INTEGRATION_SETUP_SLASH,
)
from core.tool import REGISTERED_TOOL_ATTR
from integrations.elasticsearch.client import ElasticsearchClient, ElasticsearchConfig
from integrations.opensearch.tools.opensearch_analytics_tool import (
    _map_query_opensearch_analytics,
    query_opensearch_analytics,
)
from tests.tools.conftest import BaseToolContract

_CREDENTIALS_REJECTED = "rejected the configured credentials"
_READ_NOT_ALLOWED = "isn't allowed to read this index"

# ---------------------------------------------------------------------------
# Test helpers — keep ElasticsearchClient stubbing consistent
# ---------------------------------------------------------------------------


def _install_es_stubs(
    monkeypatch: pytest.MonkeyPatch,
    search_impl: Callable[..., dict[str, Any]],
) -> dict[str, Any]:
    """Patch ``ElasticsearchClient.__init__`` AND ``search_logs`` together.

    Returns a dict that captures the ``ElasticsearchConfig`` the tool builds,
    accessible as ``captured["config"]`` after the tool is invoked. Patching
    both methods keeps every test isolated from the real client even if
    ``__init__`` ever stops being lazy (e.g. starts validating URL reachability).
    """
    captured: dict[str, Any] = {}

    def _fake_init(self: Any, config: ElasticsearchConfig) -> None:
        captured["config"] = config
        self.config = config

    monkeypatch.setattr(
        "integrations.opensearch.tools.opensearch_analytics_tool.ElasticsearchClient.__init__",
        _fake_init,
    )
    monkeypatch.setattr(
        "integrations.opensearch.tools.opensearch_analytics_tool.ElasticsearchClient.search_logs",
        search_impl,
    )
    return captured


def _ok_search(_self: Any, **_kwargs: Any) -> dict[str, Any]:
    """Default ``search_logs`` stub — succeeds with an empty result set."""
    return {"success": True, "logs": []}


# ---------------------------------------------------------------------------
# Contract — metadata, is_available, extract_params surface
# ---------------------------------------------------------------------------


class TestOpenSearchAnalyticsToolContract(BaseToolContract):
    def get_tool_under_test(self) -> Any:
        return _rt()


def _rt() -> Any:
    return getattr(query_opensearch_analytics, REGISTERED_TOOL_ATTR)


def test_is_available_true_when_url_and_verified() -> None:
    sources = {
        "opensearch": {
            "connection_verified": True,
            "url": "https://os.example.invalid",
        }
    }
    assert _rt().is_available(sources) is True


def test_is_available_false_when_connection_not_verified() -> None:
    sources = {
        "opensearch": {
            "connection_verified": False,
            "url": "https://os.example.invalid",
        }
    }
    assert _rt().is_available(sources) is False


def test_is_available_false_when_url_missing() -> None:
    sources = {"opensearch": {"connection_verified": True}}
    assert _rt().is_available(sources) is False


def test_is_available_false_when_no_opensearch_source() -> None:
    assert _rt().is_available({}) is False


def test_extract_params_strips_and_uses_defaults() -> None:
    sources = {
        "opensearch": {
            "url": "  https://os.example.invalid  ",
            "api_key": "  os-key  ",
            "index_pattern": "  logs-*  ",
            "default_query": "  service:foo  ",
            "integration_id": " os-1 ",
        }
    }
    params = _rt().extract_params(sources)
    assert params["url"] == "https://os.example.invalid"
    assert params["api_key"] == "os-key"
    assert params["index_pattern"] == "logs-*"
    assert params["query"] == "service:foo"
    assert params["time_range_minutes"] == 60  # default
    assert params["limit"] == 50
    assert params["max_results"] == 100  # _DEFAULT_MAX_RESULTS
    assert params["integration_id"] == "os-1"


def test_extract_params_falls_back_to_star_for_blank_index_and_query() -> None:
    sources = {
        "opensearch": {
            "url": "https://os.example.invalid",
            "index_pattern": "  ",
            "default_query": "",
        }
    }
    params = _rt().extract_params(sources)
    assert params["index_pattern"] == "*"
    assert params["query"] == "*"


def test_extract_params_uses_default_max_results_when_zero() -> None:
    sources = {"opensearch": {"url": "https://os.example.invalid", "max_results": 0}}
    assert _rt().extract_params(sources)["max_results"] == 100


# ---------------------------------------------------------------------------
# Validation guards — missing config
# ---------------------------------------------------------------------------


def test_a_blank_url_asks_the_user_to_run_setup() -> None:
    # Act
    result = query_opensearch_analytics(url="   ")

    # Assert
    assert result["available"] is False and result["logs"] == []
    assert result["setup_command"] == OPENSEARCH_INTEGRATION_SETUP_SLASH
    assert OPENSEARCH_INTEGRATION_SETUP_CLI in result["response_text"]
    assert OPENSEARCH_INTEGRATION_SETUP_SLASH in result["error"]


# ---------------------------------------------------------------------------
# Client config normalization — what gets passed to ElasticsearchClient
# ---------------------------------------------------------------------------


def test_client_config_normalizes_url_api_key_and_index(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _install_es_stubs(monkeypatch, _ok_search)

    query_opensearch_analytics(
        url="  https://os.example.invalid/  ",  # trailing slash + spaces stripped
        api_key="  os-key  ",
        index_pattern="logs-prod-*",
    )
    cfg = captured["config"]
    assert isinstance(cfg, ElasticsearchConfig)
    assert cfg.url == "https://os.example.invalid"
    assert cfg.api_key == "os-key"
    assert cfg.index_pattern == "logs-prod-*"


def test_client_config_treats_blank_api_key_as_none(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _install_es_stubs(monkeypatch, _ok_search)

    query_opensearch_analytics(
        url="https://os.example.invalid",
        api_key="   ",
        index_pattern="*",
    )
    assert captured["config"].api_key is None


def test_blank_index_pattern_becomes_star(monkeypatch: pytest.MonkeyPatch) -> None:
    search_kwargs: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        search_kwargs.update(kwargs)
        return {"success": True, "logs": []}

    captured = _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid", index_pattern="")
    assert captured["config"].index_pattern == "*"
    assert search_kwargs["index_pattern"] == "*"
    assert result["index_pattern"] == "*"


# ---------------------------------------------------------------------------
# Search call shape — query, time range, bounded limit
# ---------------------------------------------------------------------------


def test_search_query_defaults_to_star_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "logs": []}

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(
        url="https://os.example.invalid",
        query="",
    )
    assert captured["query"] == "*"
    assert result["query"] == "*"


def test_time_range_minutes_floor_one(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        captured.update(kwargs)
        return {"success": True, "logs": []}

    _install_es_stubs(monkeypatch, _impl)

    query_opensearch_analytics(
        url="https://os.example.invalid",
        time_range_minutes=0,
    )
    # max(1, time_range_minutes) — never zero or negative
    assert captured["time_range_minutes"] == 1


def test_bounded_limit_caps_caller_request(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        captured["limit"] = kwargs["limit"]
        # Server returns more than the cap — tool must trim to effective_limit
        return {
            "success": True,
            "logs": [{"message": f"log-{idx}"} for idx in range(20)],
        }

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(
        url="https://os.example.invalid",
        limit=999,
        max_results=5,
    )
    assert captured["limit"] == 5
    assert len(result["logs"]) == 5
    assert result["total_returned"] == 5


def test_bounded_limit_capped_by_hard_max(monkeypatch: pytest.MonkeyPatch) -> None:
    """Caller may not request more than _MAX_HARD_LIMIT (200), even via max_results."""
    captured: dict[str, Any] = {}

    def _impl(_self: Any, **kwargs: Any) -> dict[str, Any]:
        captured["limit"] = kwargs["limit"]
        return {"success": True, "logs": []}

    _install_es_stubs(monkeypatch, _impl)

    query_opensearch_analytics(
        url="https://os.example.invalid",
        limit=10_000,
        max_results=10_000,
    )
    assert captured["limit"] == 200


# ---------------------------------------------------------------------------
# Response normalization — non-dict log entries are dropped
# ---------------------------------------------------------------------------


def test_filters_non_dict_log_entries(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {
            "success": True,
            "logs": [
                {"message": "ok"},
                "garbage-string",
                {"message": "ok2"},
                None,
                42,
            ],
        }

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(
        url="https://os.example.invalid",
        max_results=10,
    )
    assert result["available"] is True
    assert result["logs"] == [{"message": "ok"}, {"message": "ok2"}]
    assert result["total_returned"] == 2


def test_handles_missing_logs_field(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"success": True}  # no 'logs' key at all

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid")
    assert result["available"] is True
    assert result["logs"] == []


def test_handles_non_list_logs_field(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"success": True, "logs": "should-be-a-list-but-isnt"}

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid")
    assert result["available"] is True
    assert result["logs"] == []


# ---------------------------------------------------------------------------
# Client error propagation
# ---------------------------------------------------------------------------


def test_propagates_client_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"success": False, "error": "auth failed"}

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid")
    assert result["available"] is False
    assert "auth failed" in result["error"]
    assert result["logs"] == []


def _cluster_answers(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Serve the real client's requests from ``answer`` instead of a live cluster."""

    def _http_client(self: ElasticsearchClient) -> httpx.Client:
        return httpx.Client(base_url=self.config.base_url, transport=httpx.MockTransport(answer))

    monkeypatch.setattr(ElasticsearchClient, "_get_client", _http_client)


def _answer(status: HTTPStatus, **content: Any) -> Callable[[httpx.Request], httpx.Response]:
    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, **content)

    return answer


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


@pytest.mark.parametrize(
    ("status", "content", "says", "never_says"),
    [
        # The OpenSearch security plugin answers a bad login with plain text.
        pytest.param(
            HTTPStatus.UNAUTHORIZED,
            {"text": "Unauthorized"},
            _CREDENTIALS_REJECTED,
            _READ_NOT_ALLOWED,
            id="unauthenticated",
        ),
        pytest.param(
            HTTPStatus.FORBIDDEN,
            {
                "json": {
                    "error": {
                        "type": "security_exception",
                        "reason": "no permissions for [indices:data/read/search] and User [name=opensre]",
                    }
                }
            },
            _READ_NOT_ALLOWED,
            _CREDENTIALS_REJECTED,
            id="forbidden",
        ),
    ],
)
def test_a_rejected_login_and_a_refused_read_each_name_their_own_fix(
    monkeypatch: pytest.MonkeyPatch,
    status: HTTPStatus,
    content: dict[str, Any],
    says: str,
    never_says: str,
) -> None:
    # Arrange
    _cluster_answers(monkeypatch, _answer(status, **content))

    # Act
    result = query_opensearch_analytics(url="https://os.example.invalid", index_pattern="logs-*")

    # Assert
    assert result["available"] is False and result["logs"] == []
    assert result["setup_command"] == OPENSEARCH_INTEGRATION_SETUP_SLASH
    assert says in result["response_text"] and never_says not in result["response_text"]
    assert OPENSEARCH_INTEGRATION_SETUP_CLI in result["response_text"]
    assert f"HTTP {status.value}" in result["error"]


def test_an_index_pattern_that_names_no_index_is_retried_not_sent_to_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 404 is usually an index name the model chose; a wildcard that matches nothing is a 200."""
    # Arrange
    _cluster_answers(
        monkeypatch,
        _answer(
            HTTPStatus.NOT_FOUND,
            json={
                "error": {"type": "index_not_found_exception", "reason": "no such index [app-logs]"}
            },
        ),
    )

    # Act
    result = query_opensearch_analytics(url="https://os.example.invalid", index_pattern="app-logs")

    # Assert
    assert result["available"] is False and result["logs"] == []
    assert "setup_command" not in result and "response_text" not in result
    assert "without index_pattern" in result["error"] and "`logs-*`" in result["error"]


@pytest.mark.parametrize(
    "answer",
    [
        _answer(HTTPStatus.SERVICE_UNAVAILABLE, json={"error": "cluster_block_exception"}),
        _timeout,
    ],
    ids=["unavailable", "timeout"],
)
def test_failures_that_pass_on_their_own_stay_plain_errors(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[httpx.Request], httpx.Response]
) -> None:
    # Arrange
    _cluster_answers(monkeypatch, answer)

    # Act
    result = query_opensearch_analytics(url="https://os.example.invalid")

    # Assert
    assert result["available"] is False and result["error"]
    assert "setup_command" not in result and "response_text" not in result


def test_a_search_that_matches_nothing_is_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a wildcard pattern that matches no index answers 200 with no hits
    _cluster_answers(
        monkeypatch,
        _answer(HTTPStatus.OK, json={"hits": {"total": {"value": 0}, "hits": []}}),
    )

    # Act
    result = query_opensearch_analytics(url="https://os.example.invalid", index_pattern="logs-*")

    # Assert
    assert result["available"] is True and result["logs"] == []
    assert "setup_command" not in result


def test_propagates_unknown_client_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def _impl(_self: Any, **_kwargs: Any) -> dict[str, Any]:
        return {"success": False}  # no 'error' field

    _install_es_stubs(monkeypatch, _impl)

    result = query_opensearch_analytics(url="https://os.example.invalid")
    assert result["available"] is False
    # Tool falls back to a generic message rather than raising
    assert "Unknown OpenSearch error" in result["error"]


# ---------------------------------------------------------------------------
# Evidence mapper
# ---------------------------------------------------------------------------


class TestMapQueryOpensearchAnalytics:
    def test_records_entry_with_log_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_opensearch_analytics(
            evidence,
            {
                "available": True,
                "total_returned": 2,
                "query": "service:checkout",
                "index_pattern": "logs-*",
                "logs": [{"message": "error 1"}, {"message": "error 2"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "query_opensearch_analytics"
        assert entries[0]["summary"] == "2 log(s) for query 'service:checkout' on 'logs-*'"

    def test_truncates_long_query_and_index_pattern_in_summary(self) -> None:
        evidence: dict[str, Any] = {}
        long_query = "service:checkout AND " + "x" * 200
        long_index_pattern = "logs-" + "y" * 200

        _map_query_opensearch_analytics(
            evidence,
            {
                "available": True,
                "total_returned": 1,
                "query": long_query,
                "index_pattern": long_index_pattern,
                "logs": [{"message": "error 1"}],
            },
            {},
        )

        summary = evidence["catalog_entries"][0]["summary"]
        assert len(summary) < len(long_query) + len(long_index_pattern)
        assert long_query not in summary
        assert long_index_pattern not in summary

    def test_records_nothing_when_no_logs(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_opensearch_analytics(
            evidence, {"available": True, "total_returned": 0, "logs": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_query_opensearch_analytics(
            evidence, {"available": False, "error": "auth failed", "logs": []}, {}
        )

        assert "catalog_entries" not in evidence
