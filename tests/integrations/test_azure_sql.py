"""Unit tests for the Azure SQL integration module."""

from app.integrations.azure_sql import (
    AzureSQLConfig,
    AzureSQLValidationResult,
    azure_sql_config_from_env,
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
