"""Tests for the SQL wrapper helper (call_db_tool_with_default_db_warning)."""

from __future__ import annotations

from app.tools.utils.sql_wrapper import call_db_tool_with_default_db_warning


class FakeConfig:
    """Fake config object for testing."""

    def __init__(self, database: str):
        self.database = database


def fake_config_resolver(database: str, host: str = "localhost") -> FakeConfig:
    """Fake config resolver that returns a FakeConfig."""
    return FakeConfig(database=database)


def fake_db_caller(config: FakeConfig) -> dict:
    """Fake DB caller that returns a simple result dict."""
    return {
        "database": config.database,
        "rows": [{"id": 1, "name": "test"}],
        "count": 1,
    }


def test_wrapper_preserves_returned_dict() -> None:
    """Test that the wrapper preserves all keys from the returned dict."""
    result = call_db_tool_with_default_db_warning(
        database="mydb",
        default_db_name="postgres",
        config_resolver=fake_config_resolver,
        resolver_kwargs={"host": "localhost"},
        db_caller=fake_db_caller,
    )
    assert result["database"] == "mydb"
    assert result["rows"] == [{"id": 1, "name": "test"}]
    assert result["count"] == 1
    assert "default_db_warning" not in result


def test_wrapper_injects_warning_when_database_is_none() -> None:
    """Test that warning is injected when database parameter is None."""
    result = call_db_tool_with_default_db_warning(
        database=None,
        default_db_name="postgres",
        config_resolver=fake_config_resolver,
        resolver_kwargs={"host": "localhost"},
        db_caller=fake_db_caller,
    )
    assert "default_db_warning" in result
    assert "defaulted to 'postgres'" in result["default_db_warning"]
    assert result["database"] == "postgres"


def test_wrapper_no_warning_when_database_provided() -> None:
    """Test that no warning is injected when database is explicitly provided."""
    result = call_db_tool_with_default_db_warning(
        database="mydb",
        default_db_name="postgres",
        config_resolver=fake_config_resolver,
        resolver_kwargs={"host": "localhost"},
        db_caller=fake_db_caller,
    )
    assert "default_db_warning" not in result
    assert result["database"] == "mydb"


def test_wrapper_passes_kwargs_to_resolver() -> None:
    """Test that resolver_kwargs are correctly passed to config_resolver."""

    def resolver_with_multiple_params(database: str, host: str, port: int = 5432) -> FakeConfig:
        config = FakeConfig(database=database)
        config.host = host
        config.port = port
        return config

    def db_caller_with_config_check(config: FakeConfig) -> dict:
        return {
            "database": config.database,
            "host": config.host,
            "port": config.port,
        }

    result = call_db_tool_with_default_db_warning(
        database="testdb",
        default_db_name="postgres",
        config_resolver=resolver_with_multiple_params,
        resolver_kwargs={"host": "remote.host", "port": 5433},
        db_caller=db_caller_with_config_check,
    )
    assert result["database"] == "testdb"
    assert result["host"] == "remote.host"
    assert result["port"] == 5433


def test_wrapper_uses_default_db_name() -> None:
    """Test that the correct default database name is used."""

    def db_caller_capturing_config(config: FakeConfig) -> dict:
        return {"database_from_config": config.database}

    result = call_db_tool_with_default_db_warning(
        database=None,
        default_db_name="master",
        config_resolver=fake_config_resolver,
        resolver_kwargs={"host": "localhost"},
        db_caller=db_caller_capturing_config,
    )
    assert result["database_from_config"] == "master"
    assert "defaulted to 'master'" in result["default_db_warning"]


def test_wrapper_handles_empty_string_database() -> None:
    """Test that empty string database is treated as provided (not defaulted)."""
    result = call_db_tool_with_default_db_warning(
        database="",
        default_db_name="postgres",
        config_resolver=fake_config_resolver,
        resolver_kwargs={"host": "localhost"},
        db_caller=fake_db_caller,
    )
    # Empty string is explicitly provided (not None), so no default warning is injected.
    assert "default_db_warning" not in result
    assert result["database"] == ""
