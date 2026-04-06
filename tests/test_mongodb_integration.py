"""Unit tests for MongoDB integration."""

import os
from unittest.mock import MagicMock, patch

from app.integrations.mongodb import (
    MongoDBConfig,
    build_mongodb_config,
    mongodb_config_from_env,
    validate_mongodb_config,
)
from app.nodes.resolve_integrations.node import _classify_integrations


class TestMongoDBConfig:
    def test_default_values(self):
        config = MongoDBConfig(connection_string="mongodb://localhost:27017")
        assert config.auth_source == "admin"
        assert config.tls is True
        assert config.timeout_seconds == 10.0
        assert config.max_results == 50

    def test_normalization(self):
        config = MongoDBConfig(
            connection_string="  mongodb://localhost:27017  ",
            database="  testdb  ",
            auth_source="  ",
        )
        assert config.connection_string == "mongodb://localhost:27017"
        assert config.database == "testdb"
        assert config.auth_source == "admin"

    def test_is_configured(self):
        assert MongoDBConfig(connection_string="mongodb://host").is_configured is True
        assert MongoDBConfig(connection_string="").is_configured is False


class TestMongoDBBuild:
    def test_build_mongodb_config(self):
        raw = {
            "connection_string": "mongodb://host",
            "database": "foo",
            "auth_source": "user_db",
            "tls": False,
        }
        config = build_mongodb_config(raw)
        assert config.connection_string == "mongodb://host"
        assert config.database == "foo"
        assert config.auth_source == "user_db"
        assert config.tls is False

    @patch.dict(os.environ, {
        "MONGODB_CONNECTION_STRING": "mongodb://env-host",
        "MONGODB_DATABASE": "env-db",
        "MONGODB_AUTH_SOURCE": "env-auth",
        "MONGODB_TLS": "false",
    })
    def test_mongodb_config_from_env(self):
        config = mongodb_config_from_env()
        assert config is not None
        assert config.connection_string == "mongodb://env-host"
        assert config.database == "env-db"
        assert config.auth_source == "env-auth"
        assert config.tls is False

    @patch.dict(os.environ, {}, clear=True)
    def test_mongodb_config_from_env_missing(self):
        assert mongodb_config_from_env() is None


class TestMongoDBValidation:
    @patch("app.integrations.mongodb._get_client")
    def test_validate_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.admin.command.return_value = {"ok": 1}
        mock_client.server_info.return_value = {"version": "6.0.5"}
        mock_get_client.return_value = mock_client

        config = MongoDBConfig(connection_string="mongodb://host", database="test")
        result = validate_mongodb_config(config)

        assert result.ok is True
        assert "6.0.5" in result.detail
        assert "test" in result.detail
        mock_client.close.assert_called_once()

    @patch("app.integrations.mongodb._get_client")
    def test_validate_ping_failure(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.admin.command.return_value = {"ok": 0}
        mock_get_client.return_value = mock_client

        config = MongoDBConfig(connection_string="mongodb://host")
        result = validate_mongodb_config(config)

        assert result.ok is False
        assert "unexpected result" in result.detail

    @patch("app.integrations.mongodb._get_client", side_effect=Exception("Conn error"))
    def test_validate_exception(self, _):
        config = MongoDBConfig(connection_string="mongodb://host")
        result = validate_mongodb_config(config)
        assert result.ok is False
        assert "Conn error" in result.detail


class TestResolveIntegrations:
    def test_classify_mongodb(self):
        integrations = [
            {
                "id": "123",
                "service": "mongodb",
                "status": "active",
                "credentials": {
                    "connection_string": "mongodb://host",
                    "database": "prod",
                }
            }
        ]
        resolved = _classify_integrations(integrations)
        assert "mongodb" in resolved
        assert resolved["mongodb"]["connection_string"] == "mongodb://host"
        assert resolved["mongodb"]["database"] == "prod"
        assert resolved["mongodb"]["auth_source"] == "admin" # default
