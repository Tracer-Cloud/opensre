"""Unit tests for the Azure SQL integration module."""

from app.integrations.azure_sql import (
    DEFAULT_AZURE_SQL_PORT,
    AzureSQLConfig,
    AzureSQLValidationResult,
    azure_sql_config_from_env,
    azure_sql_extract_params,
    azure_sql_is_available,
    build_azure_sql_config,
)


class TestAzureSQLConfig:
    """Tests for AzureSQLConfig model."""

    def test_defaults(self) -> None:
        config = AzureSQLConfig(server="myserver.database.windows.net", database="testdb")
        assert config.server == "myserver.database.windows.net"
        assert config.port == 1433
        assert config.database == "testdb"
        assert config.username == ""
        assert config.password == ""
        assert config.driver == "ODBC Driver 18 for SQL Server"
        assert config.encrypt is True
        assert config.timeout_seconds == 15.0
        assert config.max_results == 50

    def test_is_configured_with_server_and_database(self) -> None:
        config = AzureSQLConfig(server="myserver.database.windows.net", database="mydb")
        assert config.is_configured is True

    def test_is_configured_without_server(self) -> None:
        config = AzureSQLConfig(database="mydb")
        assert config.is_configured is False

    def test_is_configured_without_database(self) -> None:
        config = AzureSQLConfig(server="myserver.database.windows.net")
        assert config.is_configured is False

    def test_is_configured_without_server_and_database(self) -> None:
        config = AzureSQLConfig()
        assert config.is_configured is False

    def test_normalize_server_strips_whitespace(self) -> None:
        config = AzureSQLConfig(server="  myserver.database.windows.net  ", database="mydb")
        assert config.server == "myserver.database.windows.net"

    def test_normalize_empty_server(self) -> None:
        config = AzureSQLConfig(server="", database="mydb")
        assert config.server == ""
        assert config.is_configured is False

    def test_normalize_database_strips_whitespace(self) -> None:
        config = AzureSQLConfig(server="myserver.database.windows.net", database="  mydb  ")
        assert config.database == "mydb"

    def test_normalize_empty_database(self) -> None:
        config = AzureSQLConfig(server="myserver.database.windows.net", database="")
        assert config.database == ""
        assert config.is_configured is False

    def test_normalize_driver_default(self) -> None:
        config = AzureSQLConfig(server="s", database="d", driver="")
        assert config.driver == "ODBC Driver 18 for SQL Server"

    def test_normalize_driver_custom(self) -> None:
        config = AzureSQLConfig(server="s", database="d", driver="ODBC Driver 17 for SQL Server")
        assert config.driver == "ODBC Driver 17 for SQL Server"

    def test_custom_values(self) -> None:
        config = AzureSQLConfig(
            server="prod.database.windows.net",
            port=1434,
            database="analytics",
            username="reader",
            password="secret",
            driver="ODBC Driver 17 for SQL Server",
            encrypt=False,
            timeout_seconds=30.0,
            max_results=100,
        )
        assert config.server == "prod.database.windows.net"
        assert config.port == 1434
        assert config.database == "analytics"
        assert config.username == "reader"
        assert config.password == "secret"
        assert config.driver == "ODBC Driver 17 for SQL Server"
        assert config.encrypt is False
        assert config.timeout_seconds == 30.0
        assert config.max_results == 100

    def test_validation_result_ok(self) -> None:
        result = AzureSQLValidationResult(ok=True, detail="Connected.")
        assert result.ok is True
        assert result.detail == "Connected."

    def test_validation_result_failed(self) -> None:
        result = AzureSQLValidationResult(ok=False, detail="Connection refused.")
        assert result.ok is False
        assert result.detail == "Connection refused."


class TestBuildAzureSQLConfig:
    """Tests for build_azure_sql_config helper."""

    def test_from_dict(self) -> None:
        config = build_azure_sql_config(
            {
                "server": "myserver.database.windows.net",
                "database": "mydb",
                "port": 1434,
            }
        )
        assert config.server == "myserver.database.windows.net"
        assert config.database == "mydb"
        assert config.port == 1434

    def test_from_none(self) -> None:
        config = build_azure_sql_config(None)
        assert config.server == ""
        assert config.database == ""
        assert config.is_configured is False

    def test_from_empty_dict(self) -> None:
        config = build_azure_sql_config({})
        assert config.server == ""
        assert config.database == ""
        assert config.is_configured is False


class TestAzureSQLConfigFromEnv:
    """Tests for azure_sql_config_from_env helper."""

    def test_returns_none_without_server(self) -> None:
        import os

        old_server = os.environ.get("AZURE_SQL_SERVER")
        old_database = os.environ.get("AZURE_SQL_DATABASE")
        os.environ.pop("AZURE_SQL_SERVER", None)
        os.environ.pop("AZURE_SQL_DATABASE", None)
        try:
            result = azure_sql_config_from_env()
            assert result is None
        finally:
            if old_server is not None:
                os.environ["AZURE_SQL_SERVER"] = old_server
            if old_database is not None:
                os.environ["AZURE_SQL_DATABASE"] = old_database

    def test_returns_config_with_server_and_database(self) -> None:
        import os

        old = {
            k: os.environ.get(k)
            for k in (
                "AZURE_SQL_SERVER",
                "AZURE_SQL_DATABASE",
                "AZURE_SQL_PORT",
                "AZURE_SQL_USERNAME",
                "AZURE_SQL_PASSWORD",
            )
        }
        os.environ["AZURE_SQL_SERVER"] = "myserver.database.windows.net"
        os.environ["AZURE_SQL_DATABASE"] = "mydb"
        os.environ["AZURE_SQL_PORT"] = "1434"
        os.environ["AZURE_SQL_USERNAME"] = "admin"
        os.environ["AZURE_SQL_PASSWORD"] = "secret"
        try:
            result = azure_sql_config_from_env()
            assert result is not None
            assert result.server == "myserver.database.windows.net"
            assert result.database == "mydb"
            assert result.port == 1434
            assert result.username == "admin"
            assert result.password == "secret"
        finally:
            for k, v in old.items():
                if v is not None:
                    os.environ[k] = v
                else:
                    os.environ.pop(k, None)


class TestAzureSQLIsAvailable:
    def test_true_with_server_and_database(self) -> None:
        sources = {"azure_sql": {"server": "myserver.database.windows.net", "database": "mydb"}}
        assert azure_sql_is_available(sources) is True

    def test_false_when_missing_server(self) -> None:
        assert azure_sql_is_available({"azure_sql": {"server": "", "database": "mydb"}}) is False

    def test_false_when_missing_database(self) -> None:
        assert azure_sql_is_available({"azure_sql": {"server": "s", "database": ""}}) is False

    def test_false_when_azure_sql_key_absent(self) -> None:
        assert azure_sql_is_available({}) is False

    def test_false_when_azure_sql_is_empty_dict(self) -> None:
        assert azure_sql_is_available({"azure_sql": {}}) is False

    def test_false_when_server_is_none(self) -> None:
        assert azure_sql_is_available({"azure_sql": {"server": None, "database": "mydb"}}) is False


class TestAzureSQLExtractParams:
    def test_all_identifying_fields_returned(self) -> None:
        sources = {
            "azure_sql": {
                "server": "myserver.database.windows.net",
                "database": "mydb",
                "port": 1434,
            }
        }
        params = azure_sql_extract_params(sources)
        assert params == {
            "server": "myserver.database.windows.net",
            "database": "mydb",
            "port": 1434,
        }

    def test_credentials_are_never_surfaced(self) -> None:
        # Even if the source dict contains credentials, extract_params must only
        # expose identifying params. Credentials belong in resolve_azure_sql_config,
        # not on the LLM-visible tool signature.
        sources = {
            "azure_sql": {
                "server": "s",
                "database": "d",
                "port": 1433,
                "username": "admin",
                "password": "secret",
                "driver": "ODBC Driver 18 for SQL Server",
            }
        }
        params = azure_sql_extract_params(sources)
        assert set(params) == {"server", "database", "port"}
        assert "username" not in params
        assert "password" not in params
        assert "driver" not in params

    def test_defaults_when_keys_missing(self) -> None:
        params = azure_sql_extract_params({"azure_sql": {}})
        assert params["server"] == ""
        assert params["database"] == ""
        assert params["port"] == DEFAULT_AZURE_SQL_PORT

    def test_empty_sources_dict(self) -> None:
        params = azure_sql_extract_params({})
        assert params["server"] == ""
        assert params["database"] == ""
        assert params["port"] == DEFAULT_AZURE_SQL_PORT

    def test_server_whitespace_stripped(self) -> None:
        sources = {"azure_sql": {"server": "  myserver.database.windows.net  ", "database": "d"}}
        assert azure_sql_extract_params(sources)["server"] == "myserver.database.windows.net"

    def test_port_none_collapses_to_default(self) -> None:
        # A stored integration persisting {"port": null} must not crash
        # extract_params with TypeError from int(None). The `or` fallback
        # collapses both missing and explicit-None ports to the default.
        sources = {"azure_sql": {"server": "s", "database": "d", "port": None}}
        assert azure_sql_extract_params(sources)["port"] == DEFAULT_AZURE_SQL_PORT

    def test_port_zero_also_collapses_to_default(self) -> None:
        # Port 0 is a sentinel for "unconfigured" in this context; treat
        # the same as None to match the `or` idiom.
        sources = {"azure_sql": {"server": "s", "database": "d", "port": 0}}
        assert azure_sql_extract_params(sources)["port"] == DEFAULT_AZURE_SQL_PORT

    def test_port_as_numeric_string(self) -> None:
        sources = {"azure_sql": {"server": "s", "database": "d", "port": "1434"}}
        assert azure_sql_extract_params(sources)["port"] == 1434

    def test_server_none_returns_empty_string(self) -> None:
        # A stored integration persisting {"server": null} must not yield the
        # literal string "None" through str(None). The `or ""` fallback keeps
        # the result empty, matching the AzureSQLConfig._normalize_server validator.
        sources = {"azure_sql": {"server": None, "database": "d"}}
        assert azure_sql_extract_params(sources)["server"] == ""

    def test_database_none_returns_empty_string(self) -> None:
        sources = {"azure_sql": {"server": "s", "database": None}}
        assert azure_sql_extract_params(sources)["database"] == ""
