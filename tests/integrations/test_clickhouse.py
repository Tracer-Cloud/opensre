"""Unit tests for the ClickHouse integration module."""

from unittest.mock import MagicMock, patch

from integrations.clickhouse import (
    ClickHouseConfig,
    ClickHouseValidationResult,
    build_clickhouse_config,
    clickhouse_config_from_env,
    get_query_activity,
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


class TestGetQueryActivityIncludesFailedQueries:
    """Regression: WHERE type = 'QueryFinish' silently excluded every failed
    query (ClickHouse logs those as ExceptionBeforeStart/
    ExceptionWhileProcessing, never QueryFinish), even though the tool is
    documented to surface "recent slow / failed queries" -- confirmed live
    against a real ClickHouse instance, where a failing query never appeared
    in the results no matter the limit."""

    def test_where_clause_does_not_filter_to_query_finish_only(self) -> None:
        mock_client = MagicMock()
        mock_client.query.return_value = MagicMock(named_results=lambda: iter([]))
        with patch("integrations.clickhouse._get_client", return_value=mock_client):
            config = ClickHouseConfig(host="ch.example.com")
            get_query_activity(config, limit=10)

        sql = mock_client.query.call_args.args[0]
        assert "type = 'QueryFinish'" not in sql
        assert "type != 'QueryStart'" in sql

    def test_returns_a_real_exception_before_start_row(self) -> None:
        mock_client = MagicMock()
        mock_client.query.return_value = MagicMock(
            named_results=lambda: iter(
                [
                    {
                        "query_id": "q1",
                        "type": "ExceptionBeforeStart",
                        "query": "SELECT * FROM missing_table",
                        "query_duration_ms": 0,
                        "read_rows": 0,
                        "read_bytes": 0,
                        "result_rows": 0,
                        "memory_usage": 0,
                        "event_time": "2026-01-01 00:00:00",
                    }
                ]
            )
        )
        with patch("integrations.clickhouse._get_client", return_value=mock_client):
            config = ClickHouseConfig(host="ch.example.com")
            result = get_query_activity(config, limit=10)

        assert result["available"] is True
        assert result["queries"][0]["type"] == "ExceptionBeforeStart"
