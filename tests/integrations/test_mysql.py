"""Unit tests for the MySQL integration module."""

from app.integrations.mysql import (
    MySQLConfig,
    MySQLValidationResult,
    build_mysql_config,
    mysql_config_from_env,
)


class TestMySQLConfig:
    """Tests for MySQLConfig model."""

    def test_defaults(self) -> None:
        config = MySQLConfig(host="localhost", database="testdb")
        assert config.host == "localhost"
        assert config.port == 3306
        assert config.database == "testdb"
        assert config.username == "root"
        assert config.password == ""
        assert config.ssl_mode == "preferred"
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50

    def test_is_configured_with_host_and_database(self) -> None:
        config = MySQLConfig(host="mysql.example.com", database="mydb")
        assert config.is_configured is True

    def test_is_configured_without_host(self) -> None:
        config = MySQLConfig(database="mydb")
        assert config.is_configured is False

    def test_is_configured_without_database(self) -> None:
        config = MySQLConfig(host="localhost")
        assert config.is_configured is False

    def test_is_configured_without_host_and_database(self) -> None:
        config = MySQLConfig()
        assert config.is_configured is False

    def test_normalize_host_strips_whitespace(self) -> None:
        config = MySQLConfig(host="  mysql.example.com  ", database="mydb")
        assert config.host == "mysql.example.com"

    def test_normalize_empty_host(self) -> None:
        config = MySQLConfig(host="", database="mydb")
        assert config.host == ""
        assert config.is_configured is False

    def test_normalize_database_strips_whitespace(self) -> None:
        config = MySQLConfig(host="localhost", database="  mydb  ")
        assert config.database == "mydb"

    def test_normalize_empty_database(self) -> None:
        config = MySQLConfig(host="localhost", database="")
        assert config.database == ""
        assert config.is_configured is False

    def test_normalize_username_default(self) -> None:
        config = MySQLConfig(host="localhost", database="mydb", username="")
        assert config.username == "root"

    def test_normalize_ssl_mode_default(self) -> None:
        config = MySQLConfig(host="localhost", database="mydb", ssl_mode="")
        assert config.ssl_mode == "preferred"

    def test_custom_values(self) -> None:
        config = MySQLConfig(
            host="mysql.prod.internal",
            port=3307,
            database="analytics",
            username="reader",
            password="secret",
            ssl_mode="required",
            timeout_seconds=30.0,
            max_results=100,
        )
        assert config.host == "mysql.prod.internal"
        assert config.port == 3307
        assert config.database == "analytics"
        assert config.username == "reader"
        assert config.password == "secret"
        assert config.ssl_mode == "required"
        assert config.timeout_seconds == 30.0
        assert config.max_results == 100


class TestBuildMySQLConfig:
    """Tests for build_mysql_config helper."""

    def test_from_dict(self) -> None:
        config = build_mysql_config({"host": "mysql.example.com", "database": "mydb", "port": 3307})
        assert config.host == "mysql.example.com"
        assert config.database == "mydb"
        assert config.port == 3307

    def test_from_none(self) -> None:
        config = build_mysql_config(None)
        assert config.host == ""
        assert config.database == ""
        assert config.is_configured is False

    def test_from_empty_dict(self) -> None:
        config = build_mysql_config({})
        assert config.host == ""
        assert config.database == ""
        assert config.is_configured is False


class TestMySQLConfigFromEnv:
    """Tests for mysql_config_from_env helper."""

    def test_returns_none_without_host(self, monkeypatch) -> None:
        monkeypatch.delenv("MYSQL_HOST", raising=False)
        monkeypatch.delenv("MYSQL_DATABASE", raising=False)
        result = mysql_config_from_env()
        assert result is None

    def test_returns_none_without_database(self, monkeypatch) -> None:
        monkeypatch.setenv("MYSQL_HOST", "localhost")
        monkeypatch.delenv("MYSQL_DATABASE", raising=False)
        result = mysql_config_from_env()
        assert result is None

    def test_returns_config_with_host_and_database(self, monkeypatch) -> None:
        monkeypatch.setenv("MYSQL_HOST", "mysql.test.local")
        monkeypatch.setenv("MYSQL_PORT", "3307")
        monkeypatch.setenv("MYSQL_DATABASE", "testdb")
        monkeypatch.setenv("MYSQL_USERNAME", "testuser")
        monkeypatch.setenv("MYSQL_PASSWORD", "testpass")
        monkeypatch.setenv("MYSQL_SSL_MODE", "required")
        config = mysql_config_from_env()
        assert config is not None
        assert config.host == "mysql.test.local"
        assert config.port == 3307
        assert config.database == "testdb"
        assert config.username == "testuser"
        assert config.password == "testpass"
        assert config.ssl_mode == "required"

    def test_non_numeric_port_falls_back_to_default(self, monkeypatch) -> None:
        monkeypatch.setenv("MYSQL_HOST", "localhost")
        monkeypatch.setenv("MYSQL_DATABASE", "testdb")
        monkeypatch.setenv("MYSQL_PORT", "abc")
        config = mysql_config_from_env()
        assert config is not None
        assert config.port == 3306


class TestMySQLValidationResult:
    """Tests for MySQLValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = MySQLValidationResult(
            ok=True, detail="Connected to MySQL 8.0.32; target database: mydb."
        )
        assert result.ok is True
        assert result.detail == "Connected to MySQL 8.0.32; target database: mydb."

    def test_error_result(self) -> None:
        result = MySQLValidationResult(
            ok=False, detail="MySQL connection failed: connection refused"
        )
        assert result.ok is False
        assert result.detail == "MySQL connection failed: connection refused"
