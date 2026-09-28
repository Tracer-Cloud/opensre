"""Tests for ElasticsearchLogsTool (class-based, BaseTool subclass)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from integrations.elasticsearch.tools import ElasticsearchLogsTool
from tests.tools.conftest import BaseToolContract, mock_agent_state


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


def test_run_returns_unavailable_when_no_client() -> None:
    tool = ElasticsearchLogsTool()
    with patch("integrations.elasticsearch.tools.make_client", return_value=None):
        result = tool.run(query="test")
    assert result["available"] is False


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


def test_run_empty_logs() -> None:
    tool = ElasticsearchLogsTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {"success": True, "logs": [], "total": 0}
    with patch("integrations.elasticsearch.tools.make_client", return_value=mock_client):
        result = tool.run(query="*", url="http://localhost:9200")
    assert result["available"] is True
    assert result["logs"] == []


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
