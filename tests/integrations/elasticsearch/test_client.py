"""Tests for the Elasticsearch client and tool."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import MagicMock, patch

import httpx

from integrations.elasticsearch.tools import query_elasticsearch_logs


def test_evidence_source_includes_elasticsearch() -> None:
    assert query_elasticsearch_logs.source == "elasticsearch"


# ── Config tests ─────────────────────────────────────────────────────────────

from integrations.elasticsearch.client import ElasticsearchClient, ElasticsearchConfig


class TestElasticsearchConfig:
    def test_base_url_strips_trailing_slash(self) -> None:
        cfg = ElasticsearchConfig(url="http://localhost:9200/")
        assert cfg.base_url == "http://localhost:9200"

    def test_base_url_no_trailing_slash(self) -> None:
        cfg = ElasticsearchConfig(url="http://localhost:9200")
        assert cfg.base_url == "http://localhost:9200"

    def test_headers_no_api_key(self) -> None:
        cfg = ElasticsearchConfig(url="http://localhost:9200")
        assert cfg.headers == {"Content-Type": "application/json"}

    def test_headers_with_api_key(self) -> None:
        cfg = ElasticsearchConfig(url="http://localhost:9200", api_key="my-key")
        assert cfg.headers["Authorization"] == "ApiKey my-key"

    def test_headers_with_basic_auth(self) -> None:
        cfg = ElasticsearchConfig(url="http://localhost:9200", username="admin", password="secret")
        assert cfg.headers["Authorization"] == "Basic YWRtaW46c2VjcmV0"

    def test_headers_api_key_takes_precedence_over_basic_auth(self) -> None:
        cfg = ElasticsearchConfig(
            url="http://localhost:9200",
            api_key="my-key",
            username="admin",
            password="secret",
        )
        assert cfg.headers["Authorization"] == "ApiKey my-key"

    def test_headers_username_without_password_no_auth(self) -> None:
        cfg = ElasticsearchConfig(url="http://localhost:9200", username="admin")
        assert "Authorization" not in cfg.headers

    def test_headers_password_without_username_no_auth(self) -> None:
        cfg = ElasticsearchConfig(url="http://localhost:9200", password="secret")
        assert "Authorization" not in cfg.headers

    def test_headers_basic_auth_with_special_characters(self) -> None:
        cfg = ElasticsearchConfig(
            url="http://localhost:9200",
            username="admin",
            password="p@ss:w/ord",
        )
        assert cfg.headers["Authorization"] == "Basic YWRtaW46cEBzczp3L29yZA=="

    def test_default_index_pattern(self) -> None:
        cfg = ElasticsearchConfig(url="http://localhost:9200")
        assert cfg.index_pattern == "*"

    def test_custom_index_pattern(self) -> None:
        cfg = ElasticsearchConfig(url="http://localhost:9200", index_pattern="logs-*")
        assert cfg.index_pattern == "logs-*"


# ── Client.is_configured ──────────────────────────────────────────────────────


class TestElasticsearchClientIsConfigured:
    def test_configured_with_url_only(self) -> None:
        client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
        assert client.is_configured is True

    def test_not_configured_without_url(self) -> None:
        client = ElasticsearchClient(ElasticsearchConfig(url=""))
        assert client.is_configured is False


# ── check_security ────────────────────────────────────────────────────────────


class TestCheckSecurity:
    def test_security_disabled_returns_false(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 200

        with patch("integrations.elasticsearch.client.httpx.get", return_value=mock_resp):
            client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
            result = client.check_security()

        assert result["success"] is True
        assert result["security_enabled"] is False

    def test_security_enabled_returns_true(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 401

        with patch("integrations.elasticsearch.client.httpx.get", return_value=mock_resp):
            client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
            result = client.check_security()

        assert result["success"] is True
        assert result["security_enabled"] is True

    def test_check_security_handles_connection_error(self) -> None:
        with patch(
            "integrations.elasticsearch.client.httpx.get",
            side_effect=Exception("connection refused"),
        ):
            client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
            result = client.check_security()

        assert result["success"] is False
        assert "error" in result

    def test_check_security_unexpected_status_returns_error(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 503

        with patch("integrations.elasticsearch.client.httpx.get", return_value=mock_resp):
            client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
            result = client.check_security()

        assert result["success"] is False
        assert "Unexpected status" in result["error"]


# ── list_indices ──────────────────────────────────────────────────────────────


class TestListIndices:
    def _make_client(self) -> ElasticsearchClient:
        return ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))

    def test_returns_indices_list(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = [
            {"index": "logs-2024.01.01", "health": "green", "status": "open", "docs.count": "1000"},
            {"index": ".kibana", "health": "yellow", "status": "open", "docs.count": "5"},
        ]

        client = self._make_client()
        with patch.object(client, "_get_client") as mock_get:
            mock_http = MagicMock()
            mock_http.get.return_value = mock_resp
            mock_get.return_value = mock_http
            result = client.list_indices()

        assert result["success"] is True
        assert len(result["indices"]) == 2
        assert result["indices"][0]["index"] == "logs-2024.01.01"
        assert result["total"] == 2

    def test_list_indices_http_error(self) -> None:
        client = self._make_client()
        err_resp = MagicMock()
        err_resp.status_code = 403
        err_resp.text = "Forbidden"

        with patch.object(client, "_get_client") as mock_get:
            mock_http = MagicMock()
            mock_http.get.side_effect = httpx.HTTPStatusError(
                "403", request=MagicMock(), response=err_resp
            )
            mock_get.return_value = mock_http
            result = client.list_indices()

        assert result["success"] is False
        assert "HTTP 403" in result["error"]


# ── list_data_streams ─────────────────────────────────────────────────────────


class TestListDataStreams:
    def test_returns_data_streams(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "data_streams": [
                {"name": "logs-myapp-default", "status": "GREEN", "indices": []},
            ]
        }

        client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
        with patch.object(client, "_get_client") as mock_get:
            mock_http = MagicMock()
            mock_http.get.return_value = mock_resp
            mock_get.return_value = mock_http
            result = client.list_data_streams()

        assert result["success"] is True
        assert result["total"] == 1
        assert result["data_streams"][0]["name"] == "logs-myapp-default"

    def test_list_data_streams_error(self) -> None:
        client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
        with patch.object(client, "_get_client") as mock_get:
            mock_http = MagicMock()
            mock_http.get.side_effect = Exception("network error")
            mock_get.return_value = mock_http
            result = client.list_data_streams()

        assert result["success"] is False
        assert "error" in result


# ── search_logs ───────────────────────────────────────────────────────────────


class TestSearchLogs:
    def test_search_returns_hits(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "hits": {
                "hits": [
                    {
                        "_index": "logs-2024.01.01",
                        "_source": {
                            "@timestamp": "2024-01-01T12:00:00Z",
                            "message": "application started",
                            "level": "INFO",
                            "service": "web",
                        },
                    }
                ]
            }
        }

        client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
        with patch.object(client, "_get_client") as mock_get:
            mock_http = MagicMock()
            mock_http.post.return_value = mock_resp
            mock_get.return_value = mock_http
            result = client.search_logs(query="application started")

        assert result["success"] is True
        assert len(result["logs"]) == 1
        assert result["logs"][0]["message"] == "application started"
        assert result["logs"][0]["timestamp"] == "2024-01-01T12:00:00Z"

    def test_search_uses_custom_index_pattern(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"hits": {"hits": []}}

        client = ElasticsearchClient(
            ElasticsearchConfig(url="http://localhost:9200", index_pattern="myapp-*")
        )
        with patch.object(client, "_get_client") as mock_get:
            mock_http = MagicMock()
            mock_http.post.return_value = mock_resp
            mock_get.return_value = mock_http
            result = client.search_logs(query="error", index_pattern="custom-*")

        call_args = mock_http.post.call_args
        assert "custom-*" in call_args[0][0]
        assert result["success"] is True

    def test_search_logs_http_error(self) -> None:
        client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
        err_resp = MagicMock()
        err_resp.status_code = 400
        err_resp.text = "Bad query"

        with patch.object(client, "_get_client") as mock_get:
            mock_http = MagicMock()
            mock_http.post.side_effect = httpx.HTTPStatusError(
                "400", request=MagicMock(), response=err_resp
            )
            mock_get.return_value = mock_http
            result = client.search_logs(query="*")

        assert result["success"] is False
        assert "HTTP 400" in result["error"]
        assert result["status_code"] == HTTPStatus.BAD_REQUEST


# ── get_cluster_health ────────────────────────────────────────────────────────


class TestGetClusterHealth:
    def test_returns_cluster_health(self) -> None:
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {
            "cluster_name": "my-cluster",
            "status": "green",
            "number_of_nodes": 3,
            "number_of_data_nodes": 3,
            "active_primary_shards": 10,
            "active_shards": 20,
            "unassigned_shards": 0,
        }

        client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
        with patch.object(client, "_get_client") as mock_get:
            mock_http = MagicMock()
            mock_http.get.return_value = mock_resp
            mock_get.return_value = mock_http
            result = client.get_cluster_health()

        assert result["success"] is True
        assert result["cluster_name"] == "my-cluster"
        assert result["status"] == "green"
        assert result["number_of_nodes"] == 3

    def test_get_cluster_health_error(self) -> None:
        client = ElasticsearchClient(ElasticsearchConfig(url="http://localhost:9200"))
        with patch.object(client, "_get_client") as mock_get:
            mock_http = MagicMock()
            mock_http.get.side_effect = Exception("timeout")
            mock_get.return_value = mock_http
            result = client.get_cluster_health()

        assert result["success"] is False
        assert "error" in result


# ── package exports ───────────────────────────────────────────────────────────


def test_package_exports() -> None:
    from integrations.elasticsearch.client import ElasticsearchClient as C
    from integrations.elasticsearch.client import ElasticsearchConfig as Cfg

    assert C is not None
    assert Cfg is not None


# ── tool factory helpers ──────────────────────────────────────────────────────


def test_make_client_returns_none_without_url() -> None:
    from integrations.elasticsearch._client import make_client

    assert make_client(None) is None
    assert make_client("") is None


def test_make_client_returns_client_with_url() -> None:
    from integrations.elasticsearch._client import make_client
    from integrations.elasticsearch.client import ElasticsearchClient

    client = make_client("http://localhost:9200")
    assert isinstance(client, ElasticsearchClient)


def test_make_client_passes_api_key() -> None:
    from integrations.elasticsearch._client import make_client

    client = make_client("http://localhost:9200", api_key="abc123")
    assert client is not None
    assert client.config.api_key == "abc123"


def test_unavailable_response_shape() -> None:
    from integrations.elasticsearch._client import unavailable

    result = unavailable("elasticsearch_logs", "logs", "not configured")
    assert result["available"] is False
    assert result["source"] == "elasticsearch_logs"
    assert result["logs"] == []
    assert result["error"] == "not configured"


# ── BaseTool wrapper ──────────────────────────────────────────────────────────


def test_tool_name_and_source() -> None:
    from integrations.elasticsearch.tools import ElasticsearchLogsTool

    t = ElasticsearchLogsTool()
    assert t.name == "query_elasticsearch_logs"
    assert t.source == "elasticsearch"


def test_tool_is_available_when_connection_verified() -> None:
    # Shares the "opensearch" source (same client, same credentials) — see
    # docs/integrations/databases/opensearch.mdx: configuring OpenSearch/Elasticsearch once enables
    # both the analytics tool and this log-search tool.
    from integrations.elasticsearch.tools import ElasticsearchLogsTool

    t = ElasticsearchLogsTool()
    sources = {"opensearch": {"connection_verified": True, "url": "http://localhost:9200"}}
    assert t.is_available(sources) is True


def test_tool_is_not_available_without_source() -> None:
    from integrations.elasticsearch.tools import ElasticsearchLogsTool

    t = ElasticsearchLogsTool()
    assert t.is_available({}) is False


def test_tool_run_returns_unavailable_without_url() -> None:
    from integrations.elasticsearch.tools import ElasticsearchLogsTool

    t = ElasticsearchLogsTool()
    result = t.run(query="error", url=None)
    assert result["available"] is False
    assert result["source"] == "elasticsearch_logs"


def test_tool_run_returns_logs_on_success() -> None:
    from integrations.elasticsearch.tools import ElasticsearchLogsTool

    t = ElasticsearchLogsTool()
    mock_client = MagicMock()
    mock_client.search_logs.return_value = {
        "success": True,
        "logs": [{"timestamp": "2024-01-01T00:00:00Z", "message": "hello"}],
        "total": 1,
        "query": "error",
    }

    with patch("integrations.elasticsearch.tools.make_client", return_value=mock_client):
        result = t.run(query="error", url="http://localhost:9200")

    assert result["available"] is True
    assert result["source"] == "elasticsearch_logs"
    assert len(result["logs"]) == 1


def test_elasticsearch_evidence_mapper_with_logs_and_errors() -> None:
    from integrations.elasticsearch.tools import _map_elasticsearch_logs

    evidence: dict[str, object] = {}
    output = {
        "logs": [{"message": "hello"}, {"message": "error occurred"}],
        "error_logs": [{"message": "error occurred"}],
        "query": "status:500",
    }
    _map_elasticsearch_logs(evidence, output, {"query": "status:500"})

    assert evidence["elasticsearch_logs"] == output["logs"]
    assert evidence["elasticsearch_error_logs"] == output["error_logs"]
    assert evidence["elasticsearch_logs_query"] == "status:500"
    entries = evidence.get("catalog_entries")
    assert isinstance(entries, list)
    assert len(entries) == 1
    assert entries[0] == {
        "source": "query_elasticsearch_logs",
        "label": "Elasticsearch Logs",
        "summary": "2 logs, 1 errors",
        "url": None,
        "snippet": None,
    }


def test_elasticsearch_evidence_mapper_without_logs() -> None:
    from integrations.elasticsearch.tools import _map_elasticsearch_logs

    evidence: dict[str, object] = {}
    output = {
        "logs": [],
        "error_logs": [],
        "query": "status:500",
    }
    _map_elasticsearch_logs(evidence, output, {"query": "status:500"})

    assert evidence["elasticsearch_logs"] == []
    assert evidence["elasticsearch_error_logs"] == []
    assert evidence["elasticsearch_logs_query"] == "status:500"
    assert "catalog_entries" not in evidence


def test_elasticsearch_tool_carries_evidence_mapper() -> None:
    from tools.registry import get_registered_tool

    tool = get_registered_tool("query_elasticsearch_logs")
    assert tool is not None
    assert tool.evidence_mapper is not None
