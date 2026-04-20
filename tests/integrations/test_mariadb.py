"""Unit tests for the MariaDB integration module.

Mirrors the test_clickhouse.py pattern: config layer tests only,
no real database connections, monkeypatch for env vars.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.integrations.mariadb import (
    MariaDBConfig,
    MariaDBValidationResult,
    build_mariadb_config,
    mariadb_config_from_env,
)


class TestMariaDBConfig:
    """Tests for MariaDBConfig model."""

    def test_defaults(self) -> None:
        config = MariaDBConfig()
        assert config.host == ""
        assert config.port == 3306
        assert config.database == ""
        assert config.username == ""
        assert config.password == ""
        assert config.ssl is True
        assert config.timeout_seconds == 5
        assert config.max_results == 50
        assert config.is_configured is False

    def test_is_configured_with_host_and_database(self) -> None:
        config = MariaDBConfig(host="db.example.com", database="mydb")
        assert config.is_configured is True

    def test_is_configured_missing_host(self) -> None:
        config = MariaDBConfig(host="", database="mydb")
        assert config.is_configured is False

    def test_is_configured_missing_database(self) -> None:
        config = MariaDBConfig(host="db.example.com", database="")
        assert config.is_configured is False

    def test_normalize_host_strips_whitespace(self) -> None:
        config = MariaDBConfig(host="  db.example.com  ")
        assert config.host == "db.example.com"

    def test_normalize_host_none(self) -> None:
        config = MariaDBConfig(host=None)  # type: ignore[arg-type]
        assert config.host == ""

    def test_normalize_database_strips_whitespace(self) -> None:
        config = MariaDBConfig(database="  mydb  ")
        assert config.database == "mydb"

    def test_normalize_username_strips_whitespace(self) -> None:
        config = MariaDBConfig(host="h", database="d", username="  admin  ")
        assert config.username == "admin"

    def test_normalize_username_none(self) -> None:
        config = MariaDBConfig(host="h", database="d", username=None)  # type: ignore[arg-type]
        assert config.username == ""

    def test_normalize_password_strips_whitespace(self) -> None:
        config = MariaDBConfig(host="h", database="d", password="  s3cr3t  ")
        assert config.password == "s3cr3t"

    def test_normalize_password_none(self) -> None:
        config = MariaDBConfig(host="h", database="d", password=None)  # type: ignore[arg-type]
        assert config.password == ""

    def test_normalize_port_string(self) -> None:
        config = MariaDBConfig(host="h", database="d", port="3307")  # type: ignore[arg-type]
        assert config.port == 3307

    def test_normalize_port_invalid_string_falls_back_to_default(self) -> None:
        config = MariaDBConfig(host="h", database="d", port="not-a-port")  # type: ignore[arg-type]
        assert config.port == 3306

    def test_normalize_port_none_falls_back_to_default(self) -> None:
        config = MariaDBConfig(host="h", database="d", port=None)  # type: ignore[arg-type]
        assert config.port == 3306

    def test_timeout_seconds_is_int(self) -> None:
        config = MariaDBConfig(host="h", database="d", timeout_seconds=15)
        assert isinstance(config.timeout_seconds, int)
        assert config.timeout_seconds == 15

    def test_timeout_seconds_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            MariaDBConfig(host="h", database="d", timeout_seconds=0)

    def test_max_results_upper_boundary(self) -> None:
        config = MariaDBConfig(host="h", database="d", max_results=200)
        assert config.max_results == 200

    def test_max_results_lower_boundary(self) -> None:
        config = MariaDBConfig(host="h", database="d", max_results=1)
        assert config.max_results == 1

    def test_max_results_over_limit_raises(self) -> None:
        with pytest.raises(ValidationError):
            MariaDBConfig(host="h", database="d", max_results=201)

    def test_max_results_zero_raises(self) -> None:
        with pytest.raises(ValidationError):
            MariaDBConfig(host="h", database="d", max_results=0)

    def test_ssl_defaults_true(self) -> None:
        config = MariaDBConfig(host="h", database="d")
        assert config.ssl is True

    def test_ssl_can_be_disabled(self) -> None:
        config = MariaDBConfig(host="h", database="d", ssl=False)
        assert config.ssl is False

    def test_custom_values(self) -> None:
        config = MariaDBConfig(
            host="prod.db.internal",
            port=3307,
            database="analytics",
            username="reader",
            password="s3cr3t",
            ssl=False,
            timeout_seconds=30,
            max_results=100,
        )
        assert config.host == "prod.db.internal"
        assert config.port == 3307
        assert config.database == "analytics"
        assert config.username == "reader"
        assert config.password == "s3cr3t"
        assert config.ssl is False
        assert config.timeout_seconds == 30
        assert config.max_results == 100


class TestBuildMariaDBConfig:
    """Tests for build_mariadb_config helper."""

    def test_from_dict(self) -> None:
        config = build_mariadb_config({"host": "db.example.com", "port": 3307, "database": "mydb"})
        assert config.host == "db.example.com"
        assert config.port == 3307
        assert config.database == "mydb"

    def test_from_none(self) -> None:
        config = build_mariadb_config(None)
        assert config.host == ""
        assert config.is_configured is False

    def test_from_empty_dict(self) -> None:
        config = build_mariadb_config({})
        assert config.host == ""
        assert config.is_configured is False

    def test_ssl_false_from_dict(self) -> None:
        config = build_mariadb_config({"host": "h", "database": "d", "ssl": False})
        assert config.ssl is False

    def test_port_string_coerced(self) -> None:
        config = build_mariadb_config({"host": "h", "database": "d", "port": "3307"})
        assert config.port == 3307

    def test_password_stripped(self) -> None:
        config = build_mariadb_config({"host": "h", "database": "d", "password": "  secret  "})
        assert config.password == "secret"


class TestMariaDBConfigFromEnv:
    """Tests for mariadb_config_from_env helper."""

    def test_returns_none_without_host(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MARIADB_HOST", raising=False)
        assert mariadb_config_from_env() is None

    def test_returns_config_with_full_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARIADB_HOST", "env-host.db")
        monkeypatch.setenv("MARIADB_PORT", "3307")
        monkeypatch.setenv("MARIADB_DATABASE", "env-db")
        monkeypatch.setenv("MARIADB_USERNAME", "env-user")
        monkeypatch.setenv("MARIADB_PASSWORD", "env-pass")
        monkeypatch.setenv("MARIADB_SSL", "false")

        config = mariadb_config_from_env()

        assert config is not None
        assert config.host == "env-host.db"
        assert config.port == 3307
        assert config.database == "env-db"
        assert config.username == "env-user"
        assert config.password == "env-pass"
        assert config.ssl is False

    def test_ssl_defaults_true_when_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARIADB_HOST", "host.db")
        monkeypatch.delenv("MARIADB_SSL", raising=False)

        config = mariadb_config_from_env()

        assert config is not None
        assert config.ssl is True

    def test_ssl_true_for_all_truthy_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("true", "1", "yes"):
            monkeypatch.setenv("MARIADB_HOST", "host.db")
            monkeypatch.setenv("MARIADB_SSL", val)
            config = mariadb_config_from_env()
            assert config is not None
            assert config.ssl is True, f"Expected ssl=True for MARIADB_SSL={val!r}"

    def test_ssl_false_for_falsy_values(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for val in ("false", "0", "no"):
            monkeypatch.setenv("MARIADB_HOST", "host.db")
            monkeypatch.setenv("MARIADB_SSL", val)
            config = mariadb_config_from_env()
            assert config is not None
            assert config.ssl is False, f"Expected ssl=False for MARIADB_SSL={val!r}"

    def test_default_port_when_env_missing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARIADB_HOST", "host.db")
        monkeypatch.delenv("MARIADB_PORT", raising=False)

        config = mariadb_config_from_env()

        assert config is not None
        assert config.port == 3306

    def test_host_with_only_whitespace_returns_none(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MARIADB_HOST", "   ")
        monkeypatch.delenv("MARIADB_DATABASE", raising=False)

        result = mariadb_config_from_env()

        assert result is None


class TestMariaDBValidationResult:
    """Tests for MariaDBValidationResult dataclass."""

    def test_ok_result(self) -> None:
        result = MariaDBValidationResult(ok=True, detail="Connected to MariaDB 10.11.")
        assert result.ok is True
        assert "MariaDB" in result.detail

    def test_error_result(self) -> None:
        result = MariaDBValidationResult(ok=False, detail="Connection refused.")
        assert result.ok is False
        assert result.detail == "Connection refused."

    def test_fields_are_frozen(self) -> None:
        result = MariaDBValidationResult(ok=True, detail="ok")
        with pytest.raises((AttributeError, TypeError)):
            result.ok = False  # type: ignore[misc]
