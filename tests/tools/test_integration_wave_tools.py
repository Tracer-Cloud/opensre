"""Focused tests for integration-wave tool slices."""

from __future__ import annotations

from typing import Any

from integrations.azure.tools.azure_monitor_logs_tool import query_azure_monitor_logs
from integrations.bitbucket.tools.bitbucket_search_code_tool import _resolve_config
from integrations.openobserve.tools.openobserve_logs_tool import query_openobserve_logs
from integrations.opensearch.tools.opensearch_analytics_tool import query_opensearch_analytics
from integrations.snowflake.tools.snowflake_query_history_tool import query_snowflake_history


class _MockResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


def test_bitbucket_resolve_config_accepts_routed_instance_metadata() -> None:
    config = _resolve_config(
        "acme",
        "bb-user",
        "bb-pass",
        "https://api.bitbucket.org/2.0/",
        40,
        "bb-1",
    )

    assert config is not None
    assert config.workspace == "acme"
    assert config.base_url == "https://api.bitbucket.org/2.0"
    assert config.max_results == 40
    assert config.integration_id == "bb-1"


def test_snowflake_tool_enforces_bounded_limit(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(
        url: str, headers: dict[str, str], json: dict[str, Any], timeout: float
    ) -> _MockResponse:
        captured["url"] = url
        captured["statement"] = json["statement"]
        captured["timeout"] = timeout
        return _MockResponse({"data": [{"id": idx} for idx in range(20)]})

    monkeypatch.setattr(
        "integrations.snowflake.tools.snowflake_query_history_tool.httpx.post", _fake_post
    )

    result = query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        token="sf-token",
        query="SELECT * FROM TABLE(INFORMATION_SCHEMA.QUERY_HISTORY())",
        limit=500,
        max_results=6,
    )

    assert "LIMIT 6" in captured["statement"].upper()
    assert result["available"] is True
    assert len(result["rows"]) == 6


def test_snowflake_tool_requires_token() -> None:
    result = query_snowflake_history(
        account_identifier="xy12345.us-east-1",
        user="service-user",
        password="secret",
    )

    assert result["available"] is False
    assert result["error"] == "Missing Snowflake token."


def test_azure_tool_enforces_bounded_take_clause(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(
        url: str, headers: dict[str, str], json: dict[str, Any], timeout: float
    ) -> _MockResponse:
        captured["url"] = url
        captured["query"] = json["query"]
        return _MockResponse(
            {
                "tables": [
                    {
                        "columns": [{"name": "TimeGenerated"}, {"name": "Message"}],
                        "rows": [[f"t{idx}", f"message-{idx}"] for idx in range(10)],
                    }
                ]
            }
        )

    monkeypatch.setattr("integrations.azure.tools.azure_monitor_logs_tool.httpx.post", _fake_post)

    result = query_azure_monitor_logs(
        workspace_id="workspace-1",
        access_token="azure-token",
        query="AppTraces | order by TimeGenerated desc",
        limit=999,
        max_results=3,
    )

    assert "take 3" in captured["query"].lower()
    assert result["available"] is True
    assert len(result["rows"]) == 3


def test_openobserve_tool_caps_size_and_output(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_post(
        url: str, headers: dict[str, str], json: dict[str, Any], timeout: float
    ) -> _MockResponse:
        captured["url"] = url
        captured["size"] = json["size"]
        captured["sql"] = json["query"]["sql"]
        return _MockResponse({"hits": [{"message": f"m{idx}"} for idx in range(12)]})

    monkeypatch.setattr(
        "integrations.openobserve.tools.openobserve_logs_tool.httpx.post", _fake_post
    )

    result = query_openobserve_logs(
        base_url="https://openobserve.example.invalid",
        org="acme",
        api_token="oo-token",
        stream="app_logs",
        limit=1000,
        max_results=4,
    )

    assert captured["size"] == 4
    assert (
        captured["sql"]
        == "SELECT * FROM \"app_logs\" WHERE level = 'error' ORDER BY _timestamp DESC"
    )
    assert result["available"] is True
    assert len(result["records"]) == 4


def test_opensearch_tool_caps_limit_before_client_query(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _fake_search_logs(
        self: Any,
        query: str = "*",
        time_range_minutes: int = 60,
        limit: int = 50,
        index_pattern: str | None = None,
        timestamp_field: str = "@timestamp",
    ) -> dict[str, Any]:
        _ = (query, time_range_minutes, index_pattern, timestamp_field)
        captured["limit"] = limit
        return {"success": True, "logs": [{"message": f"log-{idx}"} for idx in range(12)]}

    monkeypatch.setattr(
        "integrations.opensearch.tools.opensearch_analytics_tool.ElasticsearchClient.search_logs",
        _fake_search_logs,
    )

    result = query_opensearch_analytics(
        url="https://opensearch.example.invalid",
        query="error",
        limit=500,
        max_results=5,
    )

    assert captured["limit"] == 5
    assert result["available"] is True
    assert len(result["logs"]) == 5


def test_opensearch_tool_forwards_basic_auth_to_elasticsearch_config(monkeypatch: Any) -> None:
    """Layer 5 / #1143: username and password must reach ElasticsearchConfig.

    Without this wiring, even though the user configures Basic Auth via the wizard
    or the legacy CLI, the AI agent's OpenSearch tool drops the credentials when
    constructing the runtime client, so the LLM cannot authenticate against the
    cluster during investigations.
    """
    captured: dict[str, Any] = {}

    class _FakeConfig:
        def __init__(
            self,
            url: str,
            api_key: str | None = None,
            username: str | None = None,
            password: str | None = None,
            index_pattern: str = "*",
        ) -> None:
            captured["url"] = url
            captured["api_key"] = api_key
            captured["username"] = username
            captured["password"] = password
            captured["index_pattern"] = index_pattern

    def _fake_search_logs(
        self: Any,
        query: str = "*",
        time_range_minutes: int = 60,
        limit: int = 50,
        index_pattern: str | None = None,
        timestamp_field: str = "@timestamp",
    ) -> dict[str, Any]:
        return {"success": True, "logs": []}

    monkeypatch.setattr(
        "integrations.opensearch.tools.opensearch_analytics_tool.ElasticsearchConfig",
        _FakeConfig,
    )
    monkeypatch.setattr(
        "integrations.opensearch.tools.opensearch_analytics_tool.ElasticsearchClient.search_logs",
        _fake_search_logs,
    )

    result = query_opensearch_analytics(
        url="https://opensearch.example.invalid",
        username="admin",
        password="secret",
        query="*",
    )

    assert captured["username"] == "admin"
    assert captured["password"] == "secret"
    assert result["available"] is True


def test_opensearch_tool_extract_params_reads_basic_auth() -> None:
    """Layer 5 / #1143: _opensearch_extract_params must surface username/password.

    These keys are populated by the catalog classifier (Layer 2) when a user
    configures Basic Auth, and the registered tool's runtime kwargs must
    include them so they reach ElasticsearchConfig.
    """
    from integrations.opensearch.tools.opensearch_analytics_tool import _opensearch_extract_params

    sources = {
        "opensearch": {
            "connection_verified": True,
            "url": "https://opensearch.example.invalid",
            "username": "admin",
            "password": "secret",
        }
    }
    params = _opensearch_extract_params(sources)
    assert params["username"] == "admin"
    assert params["password"] == "secret"
