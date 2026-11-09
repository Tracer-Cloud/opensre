"""Tests for ElasticsearchLogsTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from collections.abc import Callable
from http import HTTPStatus
from typing import Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from config.constants.opensearch import (
    OPENSEARCH_INTEGRATION_SETUP_CLI,
    OPENSEARCH_INTEGRATION_SETUP_SLASH,
)
from integrations.elasticsearch.client import ElasticsearchClient
from integrations.elasticsearch.tools import ElasticsearchLogsTool
from tests.tools.conftest import BaseToolContract, mock_agent_state

_URL = "https://es.example.invalid"
_CREDENTIALS_REJECTED = "rejected the configured credentials"
_READ_NOT_ALLOWED = "isn't allowed to read this index"


class TestElasticsearchLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return ElasticsearchLogsTool()


def test_is_available_requires_connection_verified() -> None:
    # Shares the "opensearch" source (same client, same credentials) — see
    # docs/integrations/databases/opensearch.mdx: configuring OpenSearch/Elasticsearch once enables
    # both the analytics tool and this log-search tool.
    tool = ElasticsearchLogsTool()
    assert tool.is_available({"opensearch": {"connection_verified": True}}) is True
    assert tool.is_available({"opensearch": {}}) is False
    assert tool.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    tool = ElasticsearchLogsTool()
    sources = mock_agent_state()
    params = tool.extract_params(sources)
    assert params["url"] == "http://localhost:9200"
    assert params["query"] == "*"
    assert params["index_pattern"] == "logs-*"


def _cluster_answers(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[httpx.Request], httpx.Response]
) -> None:
    """Serve the real client's requests from ``answer`` instead of a live cluster."""

    def _http_client(self: ElasticsearchClient) -> httpx.Client:
        return httpx.Client(base_url=self.config.base_url, transport=httpx.MockTransport(answer))

    monkeypatch.setattr(ElasticsearchClient, "_get_client", _http_client)


def _answer(status: HTTPStatus, body: dict[str, Any]) -> Callable[[httpx.Request], httpx.Response]:
    def answer(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, json=body)

    return answer


def _timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("timed out", request=request)


def _refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("[Errno 61] Connection refused", request=request)


def test_run_without_a_url_asks_the_user_to_run_setup() -> None:
    # Act
    result = ElasticsearchLogsTool().run(query="error", url=None)

    # Assert
    assert result["available"] is False and result["logs"] == []
    assert result["setup_command"] == OPENSEARCH_INTEGRATION_SETUP_SLASH
    assert OPENSEARCH_INTEGRATION_SETUP_CLI in result["response_text"]
    assert OPENSEARCH_INTEGRATION_SETUP_SLASH in result["error"]


@pytest.mark.parametrize(
    ("status", "body", "says", "never_says"),
    [
        pytest.param(
            HTTPStatus.UNAUTHORIZED,
            {
                "error": {
                    "type": "security_exception",
                    "reason": "unable to authenticate user [opensre] for REST request",
                }
            },
            _CREDENTIALS_REJECTED,
            _READ_NOT_ALLOWED,
            id="unauthenticated",
        ),
        pytest.param(
            HTTPStatus.FORBIDDEN,
            {
                "error": {
                    "type": "security_exception",
                    "reason": "action [indices:data/read/search] is unauthorized for user [opensre]",
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
    body: dict[str, Any],
    says: str,
    never_says: str,
) -> None:
    # Arrange
    _cluster_answers(monkeypatch, _answer(status, body))

    # Act
    result = ElasticsearchLogsTool().run(query="error", url=_URL)

    # Assert: the model keeps the cluster's detail; the user gets one line without it
    assert result["available"] is False and result["logs"] == []
    assert result["setup_command"] == OPENSEARCH_INTEGRATION_SETUP_SLASH
    assert says in result["response_text"] and never_says not in result["response_text"]
    assert OPENSEARCH_INTEGRATION_SETUP_CLI in result["response_text"]
    assert body["error"]["reason"] not in result["response_text"]
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
            {"error": {"type": "index_not_found_exception", "reason": "no such index [app-logs]"}},
        ),
    )

    # Act
    result = ElasticsearchLogsTool().run(query="error", url=_URL, index_pattern="app-logs")

    # Assert
    assert result["available"] is False and result["logs"] == []
    assert "setup_command" not in result and "response_text" not in result
    assert "without index_pattern" in result["error"] and "`logs-*`" in result["error"]


@pytest.mark.parametrize(
    "answer",
    [
        _answer(
            HTTPStatus.SERVICE_UNAVAILABLE,
            {"error": {"type": "search_phase_execution_exception", "reason": "all shards failed"}},
        ),
        _timeout,
        _refused,
    ],
    ids=["unavailable", "timeout", "refused"],
)
def test_failures_that_pass_on_their_own_stay_plain_errors(
    monkeypatch: pytest.MonkeyPatch, answer: Callable[[httpx.Request], httpx.Response]
) -> None:
    # Arrange
    _cluster_answers(monkeypatch, answer)

    # Act
    result = ElasticsearchLogsTool().run(query="error", url=_URL)

    # Assert
    assert result["available"] is False and result["error"]
    assert "setup_command" not in result and "response_text" not in result


def test_run_happy_path() -> None:
    tool = ElasticsearchLogsTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {
        "success": True,
        "logs": [
            {"message": "error in pipeline"},
            {"message": "info: job completed"},
        ],
        "total": 2,
    }
    with patch("integrations.elasticsearch.tools.make_client", return_value=mock_client):
        result = tool.run(query="*", url="http://localhost:9200")
    assert result["available"] is True
    assert len(result["logs"]) == 2
    assert len(result["error_logs"]) == 1


def test_a_search_that_matches_nothing_is_a_success(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange: a wildcard pattern that matches no index answers 200 with no hits
    _cluster_answers(
        monkeypatch,
        _answer(
            HTTPStatus.OK,
            {"timed_out": False, "hits": {"total": {"value": 0, "relation": "eq"}, "hits": []}},
        ),
    )

    # Act
    result = ElasticsearchLogsTool().run(query="level:FATAL", url=_URL, index_pattern="logs-*")

    # Assert
    assert result["available"] is True and result["logs"] == [] and result["total"] == 0
    assert "setup_command" not in result


def test_run_api_error() -> None:
    tool = ElasticsearchLogsTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {"success": False, "error": "Index not found"}
    with patch("integrations.elasticsearch.tools.make_client", return_value=mock_client):
        result = tool.run(query="*", url="http://localhost:9200")
    assert result["available"] is False


def test_extract_params_includes_basic_auth_credentials() -> None:
    """extract_params reads username/password from the opensearch source dict."""
    tool = ElasticsearchLogsTool()
    sources = mock_agent_state(
        overrides={
            "opensearch": {
                "connection_verified": True,
                "url": "https://my-cluster.example.com",
                "username": "admin",
                "password": "secret",
                "default_query": "*",
                "index_pattern": "logs-*",
            }
        }
    )
    params = tool.extract_params(sources)
    assert params["username"] == "admin"
    assert params["password"] == "secret"


def test_run_forwards_basic_auth_credentials_to_make_client() -> None:
    """run() must forward username/password to make_client so the LLM tool can authenticate."""
    tool = ElasticsearchLogsTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {"success": True, "logs": [], "total": 0}

    with patch(
        "integrations.elasticsearch.tools.make_client", return_value=mock_client
    ) as mock_make_client:
        tool.run(
            query="*",
            url="https://my-cluster.example.com",
            username="admin",
            password="secret",
        )

    mock_make_client.assert_called_once()
    _, kwargs = mock_make_client.call_args
    assert kwargs["username"] == "admin"
    assert kwargs["password"] == "secret"


def test_make_client_forwards_basic_auth_to_elasticsearch_config() -> None:
    """make_client must pass username/password into ElasticsearchConfig."""
    from integrations.elasticsearch._client import make_client

    with (
        patch("integrations.elasticsearch._client.ElasticsearchClient") as mock_client_cls,
        patch("integrations.elasticsearch._client.ElasticsearchConfig") as mock_config_cls,
    ):
        make_client(
            url="https://my-cluster.example.com",
            username="admin",
            password="secret",
        )

    mock_config_cls.assert_called_once()
    _, config_kwargs = mock_config_cls.call_args
    assert config_kwargs["username"] == "admin"
    assert config_kwargs["password"] == "secret"
    mock_client_cls.assert_called_once()
