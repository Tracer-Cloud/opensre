"""Unit tests for the ClickHouse integration module."""

from unittest.mock import MagicMock, patch

from app.integrations.clickhouse import (
    ClickHouseConfig,
    ClickHouseValidationResult,
    build_clickhouse_config,
    clickhouse_config_from_env,
    clickhouse_extract_params,
    clickhouse_is_available,
    get_query_activity,
    get_system_health,
    get_table_stats,
)


class TestClickHouseConfig:
    """Tests for ClickHouseConfig model."""

    def test_defaults(self) -> None:
        config = ClickHouseConfig(host="localhost")
        assert config.host == "localhost"
        assert config.port == 8123
        assert config.database == "default"
        assert config.username == "default"
        assert config.password == ""
        assert config.secure is False
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50

    def test_is_configured_with_host(self) -> None:
        config = ClickHouseConfig(host="ch.example.com")
        assert config.is_configured is True

    def test_is_configured_without_host(self) -> None:
        config = ClickHouseConfig()
        assert config.is_configured is False

    def test_normalize_host_strips_whitespace(self) -> None:
        config = ClickHouseConfig(host="  ch.example.com  ")
        assert config.host == "ch.example.com"

    def test_normalize_empty_host(self) -> None:
        config = ClickHouseConfig(host="")
        assert config.host == ""
        assert config.is_configured is False

    def test_normalize_database_default(self) -> None:
        config = ClickHouseConfig(host="localhost", database="")
        assert config.database == "default"

    def test_normalize_username_default(self) -> None:
        config = ClickHouseConfig(host="localhost", username="")
        assert config.username == "default"

    def test_custom_values(self) -> None:
        config = ClickHouseConfig(
            host="ch.prod.internal",
            port=9440,
            database="analytics",
            username="reader",
            password="secret",
            secure=True,
            timeout_seconds=30.0,
            max_results=100,
        )
        assert config.host == "ch.prod.internal"
        assert config.port == 9440
        assert config.database == "analytics"
        assert config.username == "reader"
        assert config.password == "secret"
        assert config.secure is True
        assert config.timeout_seconds == 30.0
        assert config.max_results == 100


class TestBuildClickHouseConfig:
    """Tests for build_clickhouse_config helper."""

    def test_from_dict(self) -> None:
        config = build_clickhouse_config({"host": "ch.example.com", "port": 9000})
        assert config.host == "ch.example.com"
        assert config.port == 9000

    def test_from_none(self) -> None:
        config = build_clickhouse_config(None)
        assert config.host == ""
        assert config.is_configured is False

    def test_from_empty_dict(self) -> None:
        config = build_clickhouse_config({})
        assert config.host == ""
        assert config.is_configured is False


class TestClickHouseConfigFromEnv:
    """Tests for clickhouse_config_from_env helper."""

    def test_returns_none_without_host(self) -> None:
        import os

        old = os.environ.get("CLICKHOUSE_HOST")
        os.environ.pop("CLICKHOUSE_HOST", None)
        try:
            result = clickhouse_config_from_env()
            assert result is None
        finally:
            if old is not None:
                os.environ["CLICKHOUSE_HOST"] = old

    def test_returns_config_with_host(self) -> None:
        import os

        os.environ["CLICKHOUSE_HOST"] = "ch.test.local"
        os.environ["CLICKHOUSE_PORT"] = "9440"
        os.environ["CLICKHOUSE_DATABASE"] = "testdb"
        os.environ["CLICKHOUSE_USER"] = "testuser"
        os.environ["CLICKHOUSE_PASSWORD"] = "testpass"
        os.environ["CLICKHOUSE_SECURE"] = "true"
        try:
            config = clickhouse_config_from_env()
            assert config is not None
            assert config.host == "ch.test.local"
            assert config.port == 9440
            assert config.database == "testdb"
            assert config.username == "testuser"
            assert config.password == "testpass"
            assert config.secure is True
        finally:
            for key in [
                "CLICKHOUSE_HOST",
                "CLICKHOUSE_PORT",
                "CLICKHOUSE_DATABASE",
                "CLICKHOUSE_USER",
                "CLICKHOUSE_PASSWORD",
                "CLICKHOUSE_SECURE",
            ]:
                os.environ.pop(key, None)


class TestClickHouseValidationResult:
    """Tests for ClickHouseValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = ClickHouseValidationResult(ok=True, detail="Connected.")
        assert result.ok is True
        assert result.detail == "Connected."

    def test_error_result(self) -> None:
        result = ClickHouseValidationResult(ok=False, detail="Connection refused.")
        assert result.ok is False
        assert result.detail == "Connection refused."


class TestClickHouseIsAvailable:
    """Tests for clickhouse_is_available helper."""

    def test_returns_true_with_connection_verified(self) -> None:
        sources = {"clickhouse": {"connection_verified": True, "host": "localhost"}}
        assert clickhouse_is_available(sources) is True

    def test_returns_false_without_connection_verified(self) -> None:
        assert clickhouse_is_available({"clickhouse": {}}) is False
        assert clickhouse_is_available({"clickhouse": {"connection_verified": False}}) is False

    def test_returns_false_without_clickhouse_key(self) -> None:
        assert clickhouse_is_available({}) is False


class TestClickHouseExtractParams:
    """Tests for clickhouse_extract_params helper."""

    def test_maps_all_fields(self) -> None:
        sources = {
            "clickhouse": {
                "host": "ch.example.com",
                "port": 9440,
                "database": "analytics",
                "username": "reader",
                "password": "secret",
                "secure": True,
            }
        }
        params = clickhouse_extract_params(sources)
        assert params["host"] == "ch.example.com"
        assert params["port"] == 9440
        assert params["database"] == "analytics"
        assert params["username"] == "reader"
        assert params["password"] == "secret"
        assert params["secure"] is True

    def test_applies_defaults(self) -> None:
        sources = {"clickhouse": {"host": "localhost"}}
        params = clickhouse_extract_params(sources)
        assert params["host"] == "localhost"
        assert params["port"] == 8123
        assert params["database"] == "default"
        assert params["username"] == "default"
        assert params["secure"] is False

    def test_handles_missing_clickhouse_key(self) -> None:
        params = clickhouse_extract_params({})
        assert params["host"] == ""
        assert params["port"] == 8123
        assert params["database"] == "default"
        assert params["username"] == "default"


class TestGetQueryActivity:
    """Tests for get_query_activity integration helper."""

    def test_returns_unavailable_when_not_configured(self) -> None:
        config = ClickHouseConfig()
        result = get_query_activity(config)
        assert result["available"] is False
        assert result["error"] == "Not configured."
        assert result["source"] == "clickhouse"

    def test_happy_path(self) -> None:
        config = ClickHouseConfig(host="localhost", port=8123)
        mock_result = MagicMock()
        mock_result.row_count = 2
        mock_result.named_results.return_value = [
            {
                "query_id": "q-1",
                "type": "QueryFinish",
                "query": "SELECT 1",
                "query_duration_ms": 10,
                "read_rows": 100,
                "read_bytes": 1024,
                "result_rows": 1,
                "memory_usage": 512,
                "event_time": "2024-01-01 00:00:00",
            },
            {
                "query_id": "q-2",
                "type": "QueryFinish",
                "query": "SELECT 2",
                "query_duration_ms": 20,
                "read_rows": 200,
                "read_bytes": 2048,
                "result_rows": 2,
                "memory_usage": 1024,
                "event_time": "2024-01-01 00:01:00",
            },
        ]
        mock_client = MagicMock()
        mock_client.query.return_value = mock_result

        with patch("app.integrations.clickhouse._get_client", return_value=mock_client) as mock_get:
            result = get_query_activity(config, limit=10)

        assert result["available"] is True
        assert result["source"] == "clickhouse"
        assert result["total_returned"] == 2
        assert len(result["queries"]) == 2
        assert result["queries"][0]["query_id"] == "q-1"
        assert result["queries"][0]["duration_ms"] == 10
        mock_get.assert_called_once()
        mock_client.close.assert_called_once()

    def test_error_path(self) -> None:
        config = ClickHouseConfig(host="localhost")
        mock_client = MagicMock()
        mock_client.query.side_effect = Exception("Connection refused")

        with patch("app.integrations.clickhouse._get_client", return_value=mock_client):
            result = get_query_activity(config)

        assert result["available"] is False
        assert "Connection refused" in result["error"]
        mock_client.close.assert_called_once()

    def test_query_truncates_long_queries(self) -> None:
        config = ClickHouseConfig(host="localhost")
        long_query = "SELECT " + "x" * 600
        mock_result = MagicMock()
        mock_result.row_count = 1
        mock_result.named_results.return_value = [
            {
                "query_id": "q-long",
                "type": "QueryFinish",
                "query": long_query,
                "query_duration_ms": 100,
                "read_rows": 0,
                "read_bytes": 0,
                "result_rows": 0,
                "memory_usage": 0,
                "event_time": "2024-01-01 00:00:00",
            }
        ]
        mock_client = MagicMock()
        mock_client.query.return_value = mock_result

        with patch("app.integrations.clickhouse._get_client", return_value=mock_client):
            result = get_query_activity(config)

        assert result["available"] is True
        assert len(result["queries"][0]["query"]) <= 500


class TestGetSystemHealth:
    """Tests for get_system_health integration helper."""

    def test_returns_unavailable_when_not_configured(self) -> None:
        config = ClickHouseConfig()
        result = get_system_health(config)
        assert result["available"] is False
        assert result["error"] == "Not configured."
        assert result["source"] == "clickhouse"

    def test_happy_path(self) -> None:
        config = ClickHouseConfig(host="localhost")

        mock_metrics = MagicMock()
        mock_metrics.row_count = 3
        mock_metrics.named_results.return_value = [
            {"metric": "Query", "value": 5},
            {"metric": "Merge", "value": 0},
            {"metric": "TCPConnection", "value": 3},
        ]

        mock_uptime = MagicMock()
        mock_uptime.row_count = 1
        mock_uptime.first_row = (864000, "24.3.3.102")

        mock_client = MagicMock()
        mock_client.query.side_effect = [mock_metrics, mock_uptime]

        with patch("app.integrations.clickhouse._get_client", return_value=mock_client):
            result = get_system_health(config)

        assert result["available"] is True
        assert result["source"] == "clickhouse"
        assert result["version"] == "24.3.3.102"
        assert result["uptime_seconds"] == 864000
        assert result["metrics"]["Query"] == 5
        assert result["metrics"]["Merge"] == 0
        assert result["metrics"]["TCPConnection"] == 3
        mock_client.close.assert_called_once()

    def test_error_path(self) -> None:
        config = ClickHouseConfig(host="localhost")
        mock_client = MagicMock()
        mock_client.query.side_effect = Exception("DNS resolution failed")

        with patch("app.integrations.clickhouse._get_client", return_value=mock_client):
            result = get_system_health(config)

        assert result["available"] is False
        assert "DNS resolution failed" in result["error"]
        mock_client.close.assert_called_once()

    def test_empty_uptime_result(self) -> None:
        config = ClickHouseConfig(host="localhost")

        mock_metrics = MagicMock()
        mock_metrics.row_count = 0
        mock_metrics.named_results.return_value = []

        mock_uptime = MagicMock()
        mock_uptime.row_count = 0
        mock_uptime.first_row = (0, "unknown")

        mock_client = MagicMock()
        mock_client.query.side_effect = [mock_metrics, mock_uptime]

        with patch("app.integrations.clickhouse._get_client", return_value=mock_client):
            result = get_system_health(config)

        assert result["available"] is True
        assert result["uptime_seconds"] == 0
        assert result["version"] == "unknown"


class TestGetTableStats:
    """Tests for get_table_stats integration helper."""

    def test_returns_unavailable_when_not_configured(self) -> None:
        config = ClickHouseConfig()
        result = get_table_stats(config)
        assert result["available"] is False
        assert result["error"] == "Not configured."
        assert result["source"] == "clickhouse"

    def test_happy_path(self) -> None:
        config = ClickHouseConfig(host="localhost", database="default")
        mock_result = MagicMock()
        mock_result.row_count = 2
        mock_result.named_results.return_value = [
            {
                "database": "default",
                "table": "users",
                "total_rows": 1000000,
                "total_bytes": 52428800,
                "part_count": 10,
                "last_modified": "2024-01-15 10:00:00",
            },
            {
                "database": "default",
                "table": "events",
                "total_rows": 5000000,
                "total_bytes": 209715200,
                "part_count": 20,
                "last_modified": "2024-01-15 09:00:00",
            },
        ]
        mock_client = MagicMock()
        mock_client.query.return_value = mock_result

        with patch("app.integrations.clickhouse._get_client", return_value=mock_client) as mock_get:
            result = get_table_stats(config, database="default", limit=10)

        assert result["available"] is True
        assert result["source"] == "clickhouse"
        assert result["database"] == "default"
        assert result["total_tables"] == 2
        assert len(result["tables"]) == 2
        assert result["tables"][0]["table"] == "users"
        assert result["tables"][0]["total_bytes"] == 52428800
        mock_get.assert_called_once()
        mock_client.close.assert_called_once()

    def test_error_path(self) -> None:
        config = ClickHouseConfig(host="localhost")
        mock_client = MagicMock()
        mock_client.query.side_effect = Exception("Table not found")

        with patch("app.integrations.clickhouse._get_client", return_value=mock_client):
            result = get_table_stats(config)

        assert result["available"] is False
        assert "Table not found" in result["error"]
        mock_client.close.assert_called_once()

    def test_uses_config_database_when_none_provided(self) -> None:
        config = ClickHouseConfig(host="localhost", database="analytics")
        mock_result = MagicMock()
        mock_result.row_count = 0
        mock_result.named_results.return_value = []
        mock_client = MagicMock()
        mock_client.query.return_value = mock_result

        with patch("app.integrations.clickhouse._get_client", return_value=mock_client):
            result = get_table_stats(config)

        assert result["available"] is True
        assert result["database"] == "analytics"
