"""Unit tests for MariaDB integration."""

from __future__ import annotations

import os
import sys
import types
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.mariadb import (
    DEFAULT_MARIADB_PORT,
    MariaDBConfig,
    _truncate,
    build_mariadb_config,
    get_global_status,
    get_innodb_status,
    get_process_list,
    get_replication_status,
    get_slow_queries,
    mariadb_config_from_env,
    mariadb_extract_params,
    mariadb_is_available,
    validate_mariadb_config,
)
from app.nodes.resolve_integrations.node import _classify_integrations

# ---------------------------------------------------------------------------
# Shared mock helpers
# ---------------------------------------------------------------------------


def _make_mock_conn(
    fetchall_rows: list | None = None,
    fetchone_row: tuple | None = None,
    description: list | None = None,
) -> tuple[MagicMock, MagicMock]:
    """Return (mock_conn, mock_cursor) wired for use with `with conn.cursor() as cur:`."""
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_cursor.fetchall.return_value = fetchall_rows or []
    mock_cursor.fetchone.return_value = fetchone_row
    mock_cursor.description = description
    mock_conn.cursor.return_value.__enter__ = lambda _self: mock_cursor
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    return mock_conn, mock_cursor


class TestMariaDBConfig:
    def test_default_values(self):
        config = MariaDBConfig(host="localhost", database="testdb")
        assert config.port == 3306
        assert config.ssl is True
        assert config.timeout_seconds == 5
        assert config.max_results == 50
        assert config.username == ""
        assert config.password == ""

    def test_normalization(self):
        config = MariaDBConfig(
            host="  db.example.com  ",
            database="  testdb  ",
            username="  admin  ",
        )
        assert config.host == "db.example.com"
        assert config.database == "testdb"
        assert config.username == "admin"

    def test_is_configured(self):
        assert MariaDBConfig(host="host", database="db").is_configured is True
        assert MariaDBConfig(host="", database="db").is_configured is False
        assert MariaDBConfig(host="host", database="").is_configured is False


class TestMariaDBBuild:
    def test_build_mariadb_config(self):
        raw = {
            "host": "db.example.com",
            "port": 3307,
            "database": "foo",
            "username": "admin",
            "password": "secret",
            "ssl": False,
        }
        config = build_mariadb_config(raw)
        assert config.host == "db.example.com"
        assert config.port == 3307
        assert config.database == "foo"
        assert config.username == "admin"
        assert config.ssl is False

    @patch.dict(os.environ, {
        "MARIADB_HOST": "env-host",
        "MARIADB_PORT": "3307",
        "MARIADB_DATABASE": "env-db",
        "MARIADB_USERNAME": "env-user",
        "MARIADB_PASSWORD": "env-pass",
        "MARIADB_SSL": "false",
    })
    def test_mariadb_config_from_env(self):
        config = mariadb_config_from_env()
        assert config is not None
        assert config.host == "env-host"
        assert config.port == 3307
        assert config.database == "env-db"
        assert config.username == "env-user"
        assert config.ssl is False

    @patch.dict(os.environ, {}, clear=True)
    def test_mariadb_config_from_env_missing(self):
        assert mariadb_config_from_env() is None


class TestMariaDBValidation:
    @patch("app.integrations.mariadb._get_connection")
    def test_validate_success(self, mock_get_conn):
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = ("10.11.6-MariaDB",)
        mock_conn.cursor.return_value.__enter__ = lambda _self: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        config = MariaDBConfig(host="host", database="test", username="user")
        result = validate_mariadb_config(config)

        assert result.ok is True
        assert "10.11.6-MariaDB" in result.detail
        assert "test" in result.detail
        mock_conn.close.assert_called_once()

    @patch("app.integrations.mariadb._get_connection", side_effect=Exception("Conn error"))
    def test_validate_exception(self, _):
        config = MariaDBConfig(host="host", database="test", username="user")
        result = validate_mariadb_config(config)
        assert result.ok is False
        assert "Conn error" in result.detail

    def test_validate_missing_host(self):
        config = MariaDBConfig(host="", database="test")
        result = validate_mariadb_config(config)
        assert result.ok is False
        assert "required" in result.detail

    def test_validate_missing_database(self):
        config = MariaDBConfig(host="host", database="")
        result = validate_mariadb_config(config)
        assert result.ok is False
        assert "required" in result.detail


class TestResolveIntegrations:
    def test_classify_mariadb(self):
        integrations = [
            {
                "id": "123",
                "service": "mariadb",
                "status": "active",
                "credentials": {
                    "host": "db.example.com",
                    "port": 3306,
                    "database": "prod",
                    "username": "admin",
                    "password": "secret",
                }
            }
        ]
        resolved = _classify_integrations(integrations)
        assert "mariadb" in resolved
        assert resolved["mariadb"]["host"] == "db.example.com"
        assert resolved["mariadb"]["database"] == "prod"
        assert resolved["mariadb"]["port"] == 3306

    def test_classify_mariadb_skipped_without_host(self):
        integrations = [
            {
                "id": "789",
                "service": "mariadb",
                "status": "active",
                "credentials": {
                    "host": "",
                    "database": "prod",
                    "username": "admin",
                }
            }
        ]
        resolved = _classify_integrations(integrations)
        assert "mariadb" not in resolved

    def test_classify_mariadb_skipped_without_database(self):
        integrations = [
            {
                "id": "456",
                "service": "mariadb",
                "status": "active",
                "credentials": {
                    "host": "db.example.com",
                    "database": "",
                    "username": "admin",
                }
            }
        ]
        resolved = _classify_integrations(integrations)
        assert "mariadb" not in resolved


# ---------------------------------------------------------------------------
# Truncation helper
# ---------------------------------------------------------------------------


class TestTruncate:
    def test_short_string_unchanged(self) -> None:
        assert _truncate("SELECT 1") == "SELECT 1"

    def test_exact_limit_unchanged(self) -> None:
        text = "x" * 200
        assert _truncate(text) == text

    def test_over_limit_truncated(self) -> None:
        result = _truncate("x" * 201)
        assert result.endswith("...")
        assert len(result) == 203  # 200 chars + "..."

    def test_empty_string(self) -> None:
        assert _truncate("") == ""

    def test_custom_max_len(self) -> None:
        assert _truncate("hello world", max_len=5) == "hello..."

    def test_very_long_string(self) -> None:
        result = _truncate("A" * 10_000)
        assert result.endswith("...")
        assert len(result) == 203


# ---------------------------------------------------------------------------
# Helper utilities
# ---------------------------------------------------------------------------


class TestMariaDBIsAvailable:
    def test_true_with_host_and_database(self) -> None:
        assert mariadb_is_available({"mariadb": {"host": "db.example.com", "database": "prod"}}) is True

    def test_false_when_missing_host(self) -> None:
        assert mariadb_is_available({"mariadb": {"host": "", "database": "prod"}}) is False

    def test_false_when_missing_database(self) -> None:
        assert mariadb_is_available({"mariadb": {"host": "h", "database": ""}}) is False

    def test_false_when_mariadb_key_absent(self) -> None:
        assert mariadb_is_available({}) is False

    def test_false_when_mariadb_is_empty_dict(self) -> None:
        assert mariadb_is_available({"mariadb": {}}) is False

    def test_false_when_host_is_none(self) -> None:
        assert mariadb_is_available({"mariadb": {"host": None, "database": "prod"}}) is False


class TestMariaDBExtractParams:
    def test_all_fields_returned(self) -> None:
        sources = {"mariadb": {"host": "h", "database": "d", "username": "u",
                               "password": "p", "port": 3307, "ssl": False}}
        params = mariadb_extract_params(sources)
        assert params["host"] == "h"
        assert params["database"] == "d"
        assert params["username"] == "u"
        assert params["password"] == "p"
        assert params["port"] == 3307
        assert params["ssl"] is False

    def test_defaults_when_keys_missing(self) -> None:
        params = mariadb_extract_params({"mariadb": {}})
        assert params["host"] == ""
        assert params["port"] == DEFAULT_MARIADB_PORT
        assert params["ssl"] is True

    def test_empty_sources_dict(self) -> None:
        params = mariadb_extract_params({})
        assert params["host"] == ""
        assert params["port"] == DEFAULT_MARIADB_PORT


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


class TestGetProcessList:
    def _config(self) -> MariaDBConfig:
        return MariaDBConfig(host="host", database="db", username="user")

    @patch("app.integrations.mariadb._get_connection")
    def test_returns_process_list_on_success(self, mock_get_conn: MagicMock) -> None:
        rows = [(1, "root", "localhost", "mydb", "Query", 5, "executing", "SELECT sleep(5)")]
        mock_conn, _ = _make_mock_conn(fetchall_rows=rows)
        mock_get_conn.return_value = mock_conn

        result = get_process_list(self._config())

        assert result["available"] is True
        assert result["source"] == "mariadb"
        assert result["total_processes"] == 1
        proc = result["processes"][0]
        assert proc["id"] == 1
        assert proc["user"] == "root"
        assert proc["command"] == "Query"
        assert proc["time_secs"] == 5
        assert proc["query"] == "SELECT sleep(5)"
        mock_conn.close.assert_called_once()

    @patch("app.integrations.mariadb._get_connection")
    def test_query_text_is_truncated(self, mock_get_conn: MagicMock) -> None:
        long_query = "SELECT " + "x" * 300
        rows = [(1, "root", "localhost", "db", "Query", 1, "", long_query)]
        mock_conn, _ = _make_mock_conn(fetchall_rows=rows)
        mock_get_conn.return_value = mock_conn

        result = get_process_list(self._config())

        assert result["processes"][0]["query"].endswith("...")
        assert len(result["processes"][0]["query"]) == 203

    @patch("app.integrations.mariadb._get_connection")
    def test_empty_process_list(self, mock_get_conn: MagicMock) -> None:
        mock_conn, _ = _make_mock_conn(fetchall_rows=[])
        mock_get_conn.return_value = mock_conn

        result = get_process_list(self._config())

        assert result["available"] is True
        assert result["total_processes"] == 0
        assert result["processes"] == []

    @patch("app.integrations.mariadb._get_connection")
    def test_max_results_override(self, mock_get_conn: MagicMock) -> None:
        mock_conn, mock_cursor = _make_mock_conn(fetchall_rows=[])
        mock_get_conn.return_value = mock_conn

        get_process_list(self._config(), max_results=10)

        sql_call = mock_cursor.execute.call_args
        assert sql_call[0][1] == (10,)

    @patch("app.integrations.mariadb._get_connection", side_effect=Exception("timeout"))
    def test_exception_returns_error(self, _: MagicMock) -> None:
        result = get_process_list(self._config())
        assert result["available"] is False
        assert "timeout" in result["error"]

    def test_not_configured_returns_error(self) -> None:
        result = get_process_list(MariaDBConfig())
        assert result["available"] is False
        assert "Not configured" in result["error"]


class TestGetGlobalStatus:
    def _config(self) -> MariaDBConfig:
        return MariaDBConfig(host="host", database="db", username="user")

    @patch("app.integrations.mariadb._get_connection")
    def test_returns_curated_metrics(self, mock_get_conn: MagicMock) -> None:
        all_status = [
            ("Threads_connected", "5"),
            ("Threads_running", "2"),
            ("Uptime", "86400"),
            ("Slow_queries", "10"),
            ("SomeOtherVar", "ignored"),
        ]
        mock_conn, _ = _make_mock_conn(fetchall_rows=all_status)
        mock_get_conn.return_value = mock_conn

        result = get_global_status(self._config())

        assert result["available"] is True
        assert result["source"] == "mariadb"
        assert result["metrics"]["Threads_connected"] == "5"
        assert "Uptime" in result["metrics"]
        assert "SomeOtherVar" not in result["metrics"]
        mock_conn.close.assert_called_once()

    @patch("app.integrations.mariadb._get_connection")
    def test_missing_keys_skipped_gracefully(self, mock_get_conn: MagicMock) -> None:
        mock_conn, _ = _make_mock_conn(fetchall_rows=[("Uptime", "3600")])
        mock_get_conn.return_value = mock_conn

        result = get_global_status(self._config())

        assert result["available"] is True
        assert result["metrics"]["Uptime"] == "3600"

    @patch("app.integrations.mariadb._get_connection", side_effect=Exception("access denied"))
    def test_exception_returns_error(self, _: MagicMock) -> None:
        result = get_global_status(self._config())
        assert result["available"] is False
        assert "access denied" in result["error"]

    def test_not_configured_returns_error(self) -> None:
        result = get_global_status(MariaDBConfig())
        assert result["available"] is False


class TestGetInnoDBStatus:
    def _config(self) -> MariaDBConfig:
        return MariaDBConfig(host="host", database="db", username="user")

    @patch("app.integrations.mariadb._get_connection")
    def test_returns_innodb_status_text(self, mock_get_conn: MagicMock) -> None:
        status_text = "================\nBUFFER POOL AND MEMORY\n================"
        mock_conn, _ = _make_mock_conn(fetchone_row=("InnoDB", "", status_text))
        mock_get_conn.return_value = mock_conn

        result = get_innodb_status(self._config())

        assert result["available"] is True
        assert result["source"] == "mariadb"
        assert "BUFFER POOL" in result["innodb_status"]
        mock_conn.close.assert_called_once()

    @patch("app.integrations.mariadb._get_connection")
    def test_long_status_text_truncated(self, mock_get_conn: MagicMock) -> None:
        long_text = "x" * 5000
        mock_conn, _ = _make_mock_conn(fetchone_row=("InnoDB", "", long_text))
        mock_get_conn.return_value = mock_conn

        result = get_innodb_status(self._config())

        assert result["available"] is True
        assert result["innodb_status"].endswith("(truncated)")
        assert len(result["innodb_status"]) < 5000

    @patch("app.integrations.mariadb._get_connection")
    def test_none_row_returns_empty_string(self, mock_get_conn: MagicMock) -> None:
        mock_conn, _ = _make_mock_conn(fetchone_row=None)
        mock_get_conn.return_value = mock_conn

        result = get_innodb_status(self._config())

        assert result["available"] is True
        assert result["innodb_status"] == ""

    @patch("app.integrations.mariadb._get_connection", side_effect=Exception("no privilege"))
    def test_exception_returns_error(self, _: MagicMock) -> None:
        result = get_innodb_status(self._config())
        assert result["available"] is False
        assert "no privilege" in result["error"]

    def test_not_configured_returns_error(self) -> None:
        result = get_innodb_status(MariaDBConfig())
        assert result["available"] is False


class TestGetSlowQueries:
    def _config(self) -> MariaDBConfig:
        return MariaDBConfig(host="host", database="mydb", username="user")

    @patch("app.integrations.mariadb._get_connection")
    def test_returns_slow_queries_when_perf_schema_enabled(self, mock_get_conn: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        query_rows = [("SELECT * FROM users", 100, 50.5, 5050.0, 1000, 100)]
        mock_cursor.fetchone.side_effect = [(1,)]
        mock_cursor.fetchall.return_value = query_rows
        mock_conn.cursor.return_value.__enter__ = lambda _self: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        result = get_slow_queries(self._config())

        assert result["available"] is True
        assert result["source"] == "mariadb"
        assert result["total_queries"] == 1
        q = result["queries"][0]
        assert q["digest_text"] == "SELECT * FROM users"
        assert q["count"] == 100
        assert q["avg_time_ms"] == pytest.approx(50.5)
        assert q["rows_examined"] == 1000
        mock_conn.close.assert_called_once()

    @patch("app.integrations.mariadb._get_connection")
    def test_returns_note_when_perf_schema_disabled(self, mock_get_conn: MagicMock) -> None:
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.fetchone.return_value = (0,)
        mock_conn.cursor.return_value.__enter__ = lambda _self: mock_cursor
        mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
        mock_get_conn.return_value = mock_conn

        result = get_slow_queries(self._config())

        assert result["available"] is True
        assert "note" in result
        assert "performance_schema" in result["note"]
        assert result["queries"] == []

    @patch("app.integrations.mariadb._get_connection", side_effect=Exception("query error"))
    def test_exception_returns_error(self, _: MagicMock) -> None:
        result = get_slow_queries(self._config())
        assert result["available"] is False
        assert "query error" in result["error"]

    def test_not_configured_returns_error(self) -> None:
        result = get_slow_queries(MariaDBConfig())
        assert result["available"] is False


class TestGetReplicationStatus:
    def _config(self) -> MariaDBConfig:
        return MariaDBConfig(host="host", database="db", username="user")

    @patch("app.integrations.mariadb._get_connection")
    def test_returns_channels_on_success(self, mock_get_conn: MagicMock) -> None:
        columns = ["Slave_IO_Running", "Slave_SQL_Running", "Seconds_Behind_Master",
                   "Last_Error", "Last_Errno", "Master_Host", "Master_Port",
                   "Master_Log_File", "Relay_Log_Space", "Exec_Master_Log_Pos", "Connection_name"]
        row = ("Yes", "Yes", 0, "", 0, "primary.db", 3306, "binlog.000001", 1024, 512, "")
        mock_conn, _ = _make_mock_conn(
            fetchall_rows=[row],
            description=[(col,) for col in columns],
        )
        mock_get_conn.return_value = mock_conn

        result = get_replication_status(self._config())

        assert result["available"] is True
        assert result["source"] == "mariadb"
        assert len(result["channels"]) == 1
        ch = result["channels"][0]
        assert ch["Slave_IO_Running"] == "Yes"
        assert ch["Master_Host"] == "primary.db"
        mock_conn.close.assert_called_once()

    @patch("app.integrations.mariadb._get_connection")
    def test_not_replica_returns_note(self, mock_get_conn: MagicMock) -> None:
        mock_conn, _ = _make_mock_conn(fetchall_rows=[], description=None)
        mock_get_conn.return_value = mock_conn

        result = get_replication_status(self._config())

        assert result["available"] is True
        assert "not configured as a replica" in result["note"]
        assert result["channels"] == []

    @patch("app.integrations.mariadb._get_connection")
    def test_fallback_to_show_slave_status(self, mock_get_conn: MagicMock) -> None:
        # Stub pymysql so this test does not require PyMySQL at import time (matches the
        # dynamic `import pymysql` inside get_replication_status error handling).
        class _ProgrammingError(Exception):
            pass

        _fake_err = types.ModuleType("pymysql.err")
        _fake_err.ProgrammingError = _ProgrammingError
        _fake_pymysql = types.ModuleType("pymysql")
        _fake_pymysql.err = _fake_err
        _prior = sys.modules.get("pymysql")
        sys.modules["pymysql"] = _fake_pymysql
        try:
            mock_conn = MagicMock()
            mock_cursor = MagicMock()
            columns = ["Slave_IO_Running", "Slave_SQL_Running"]
            row = ("Yes", "Yes")
            execute_call_count = 0

            def execute_side_effect(stmt: str, *args: object, **kwargs: object) -> None:
                nonlocal execute_call_count
                execute_call_count += 1
                if "ALL SLAVES" in stmt:
                    raise _ProgrammingError(1064, "syntax error")

            mock_cursor.execute.side_effect = execute_side_effect
            mock_cursor.fetchall.return_value = [row]
            mock_cursor.description = [(col,) for col in columns]
            mock_conn.cursor.return_value.__enter__ = lambda _self: mock_cursor
            mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
            mock_get_conn.return_value = mock_conn

            result = get_replication_status(self._config())

            assert result["available"] is True
            assert len(result["channels"]) == 1
            assert execute_call_count == 2
        finally:
            if _prior is not None:
                sys.modules["pymysql"] = _prior
            else:
                sys.modules.pop("pymysql", None)

    @patch("app.integrations.mariadb._get_connection")
    def test_partial_columns_handled(self, mock_get_conn: MagicMock) -> None:
        columns = ["Slave_IO_Running", "Slave_SQL_Running"]
        mock_conn, _ = _make_mock_conn(
            fetchall_rows=[("Yes", "Yes")],
            description=[(col,) for col in columns],
        )
        mock_get_conn.return_value = mock_conn

        result = get_replication_status(self._config())

        assert result["available"] is True
        ch = result["channels"][0]
        assert ch["Slave_IO_Running"] == "Yes"
        assert "Master_Host" not in ch

    @patch("app.integrations.mariadb._get_connection", side_effect=Exception("network error"))
    def test_exception_returns_error(self, _: MagicMock) -> None:
        result = get_replication_status(self._config())
        assert result["available"] is False
        assert "network error" in result["error"]

    def test_not_configured_returns_error(self) -> None:
        result = get_replication_status(MariaDBConfig())
        assert result["available"] is False
