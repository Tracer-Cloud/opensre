"""Unit tests for the PostgreSQL integration module."""

from app.integrations.postgresql import (
    PostgreSQLConfig,
    PostgreSQLValidationResult,
    build_postgresql_config,
    postgresql_config_from_env,
)


class TestPostgreSQLConfig:
    """Tests for PostgreSQLConfig model."""

    def test_defaults(self) -> None:
        config = PostgreSQLConfig(host="localhost", database="testdb")
        assert config.host == "localhost"
        assert config.port == 5432
        assert config.database == "testdb"
        assert config.username == "postgres"
        assert config.password == ""
        assert config.ssl_mode == "prefer"
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50

    def test_is_configured_with_host_and_database(self) -> None:
        config = PostgreSQLConfig(host="pg.example.com", database="mydb")
        assert config.is_configured is True

    def test_is_configured_without_host(self) -> None:
        config = PostgreSQLConfig(database="mydb")
        assert config.is_configured is False

    def test_is_configured_without_database(self) -> None:
        config = PostgreSQLConfig(host="localhost")
        assert config.is_configured is False

    def test_is_configured_without_host_and_database(self) -> None:
        config = PostgreSQLConfig()
        assert config.is_configured is False

    def test_normalize_host_strips_whitespace(self) -> None:
        config = PostgreSQLConfig(host="  pg.example.com  ", database="mydb")
        assert config.host == "pg.example.com"

    def test_normalize_empty_host(self) -> None:
        config = PostgreSQLConfig(host="", database="mydb")
        assert config.host == ""
        assert config.is_configured is False

    def test_normalize_database_strips_whitespace(self) -> None:
        config = PostgreSQLConfig(host="localhost", database="  mydb  ")
        assert config.database == "mydb"

    def test_normalize_empty_database(self) -> None:
        config = PostgreSQLConfig(host="localhost", database="")
        assert config.database == ""
        assert config.is_configured is False

    def test_normalize_username_default(self) -> None:
        config = PostgreSQLConfig(host="localhost", database="mydb", username="")
        assert config.username == "postgres"

    def test_normalize_ssl_mode_default(self) -> None:
        config = PostgreSQLConfig(host="localhost", database="mydb", ssl_mode="")
        assert config.ssl_mode == "prefer"

    def test_custom_values(self) -> None:
        config = PostgreSQLConfig(
            host="pg.prod.internal",
            port=5433,
            database="analytics",
            username="reader",
            password="secret",
            ssl_mode="require",
            timeout_seconds=30.0,
            max_results=100,
        )
        assert config.host == "pg.prod.internal"
        assert config.port == 5433
        assert config.database == "analytics"
        assert config.username == "reader"
        assert config.password == "secret"
        assert config.ssl_mode == "require"
        assert config.timeout_seconds == 30.0
        assert config.max_results == 100


class TestBuildPostgreSQLConfig:
    """Tests for build_postgresql_config helper."""

    def test_from_dict(self) -> None:
        config = build_postgresql_config({"host": "pg.example.com", "database": "mydb", "port": 5433})
        assert config.host == "pg.example.com"
        assert config.database == "mydb"
        assert config.port == 5433

    def test_from_none(self) -> None:
        config = build_postgresql_config(None)
        assert config.host == ""
        assert config.database == ""
        assert config.is_configured is False

    def test_from_empty_dict(self) -> None:
        config = build_postgresql_config({})
        assert config.host == ""
        assert config.database == ""
        assert config.is_configured is False


class TestPostgreSQLConfigFromEnv:
    """Tests for postgresql_config_from_env helper."""

    def test_returns_none_without_host(self) -> None:
        import os

        old_host = os.environ.get("POSTGRESQL_HOST")
        old_database = os.environ.get("POSTGRESQL_DATABASE")
        os.environ.pop("POSTGRESQL_HOST", None)
        os.environ.pop("POSTGRESQL_DATABASE", None)
        try:
            result = postgresql_config_from_env()
            assert result is None
        finally:
            if old_host is not None:
                os.environ["POSTGRESQL_HOST"] = old_host
            if old_database is not None:
                os.environ["POSTGRESQL_DATABASE"] = old_database

    def test_returns_none_without_database(self) -> None:
        import os

        old_host = os.environ.get("POSTGRESQL_HOST")
        old_database = os.environ.get("POSTGRESQL_DATABASE")
        os.environ["POSTGRESQL_HOST"] = "localhost"
        os.environ.pop("POSTGRESQL_DATABASE", None)
        try:
            result = postgresql_config_from_env()
            assert result is None
        finally:
            if old_host is not None:
                os.environ["POSTGRESQL_HOST"] = old_host
            else:
                os.environ.pop("POSTGRESQL_HOST", None)
            if old_database is not None:
                os.environ["POSTGRESQL_DATABASE"] = old_database

    def test_returns_config_with_host_and_database(self) -> None:
        import os

        os.environ["POSTGRESQL_HOST"] = "pg.test.local"
        os.environ["POSTGRESQL_PORT"] = "5433"
        os.environ["POSTGRESQL_DATABASE"] = "testdb"
        os.environ["POSTGRESQL_USERNAME"] = "testuser"
        os.environ["POSTGRESQL_PASSWORD"] = "testpass"
        os.environ["POSTGRESQL_SSL_MODE"] = "require"
        try:
            config = postgresql_config_from_env()
            assert config is not None
            assert config.host == "pg.test.local"
            assert config.port == 5433
            assert config.database == "testdb"
            assert config.username == "testuser"
            assert config.password == "testpass"
            assert config.ssl_mode == "require"
        finally:
            for key in [
                "POSTGRESQL_HOST",
                "POSTGRESQL_PORT",
                "POSTGRESQL_DATABASE",
                "POSTGRESQL_USERNAME",
                "POSTGRESQL_PASSWORD",
                "POSTGRESQL_SSL_MODE",
            ]:
                os.environ.pop(key, None)


class TestPostgreSQLValidationResult:
    """Tests for PostgreSQLValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = PostgreSQLValidationResult(ok=True, detail="Connected to PostgreSQL 16.1; target database: mydb.")
        assert result.ok is True
        assert result.detail == "Connected to PostgreSQL 16.1; target database: mydb."

    def test_error_result(self) -> None:
        result = PostgreSQLValidationResult(ok=False, detail="PostgreSQL connection failed: connection refused")
        assert result.ok is False
        assert result.detail == "PostgreSQL connection failed: connection refused"
