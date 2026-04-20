"""Unit tests for MongoDB Atlas integration."""

import os
from unittest.mock import MagicMock, patch

from app.integrations.mongodb_atlas import (
    MongoDBAtlasConfig,
    build_mongodb_atlas_config,
    mongodb_atlas_config_from_env,
    validate_mongodb_atlas_config,
)
from app.nodes.resolve_integrations.node import _classify_integrations


class TestMongoDBAtlasConfig:
    def test_default_values(self):
        config = MongoDBAtlasConfig(api_public_key="pub", api_private_key="priv", project_id="proj")
        assert config.base_url == "https://cloud.mongodb.com/api/atlas/v2"
        assert config.timeout_seconds == 15.0
        assert config.max_results == 50

    def test_normalization(self):
        config = MongoDBAtlasConfig(
            api_public_key="  pub  ",
            api_private_key="  priv  ",
            project_id="  proj123  ",
            base_url="  https://cloud.mongodb.com/api/atlas/v2/  ",
        )
        assert config.api_public_key == "pub"
        assert config.api_private_key == "priv"
        assert config.project_id == "proj123"
        assert config.base_url == "https://cloud.mongodb.com/api/atlas/v2"

    def test_is_configured(self):
        assert (
            MongoDBAtlasConfig(
                api_public_key="pub", api_private_key="priv", project_id="proj"
            ).is_configured
            is True
        )
        assert (
            MongoDBAtlasConfig(
                api_public_key="", api_private_key="priv", project_id="proj"
            ).is_configured
            is False
        )
        assert (
            MongoDBAtlasConfig(
                api_public_key="pub", api_private_key="", project_id="proj"
            ).is_configured
            is False
        )
        assert (
            MongoDBAtlasConfig(
                api_public_key="pub", api_private_key="priv", project_id=""
            ).is_configured
            is False
        )


class TestMongoDBAtlasBuild:
    def test_build_config(self):
        raw = {
            "api_public_key": "pub",
            "api_private_key": "priv",
            "project_id": "proj",
        }
        config = build_mongodb_atlas_config(raw)
        assert config.api_public_key == "pub"
        assert config.project_id == "proj"

    @patch.dict(
        os.environ,
        {
            "MONGODB_ATLAS_PUBLIC_KEY": "env-pub",
            "MONGODB_ATLAS_PRIVATE_KEY": "env-priv",
            "MONGODB_ATLAS_PROJECT_ID": "env-proj",
        },
    )
    def test_config_from_env(self):
        config = mongodb_atlas_config_from_env()
        assert config is not None
        assert config.api_public_key == "env-pub"
        assert config.api_private_key == "env-priv"
        assert config.project_id == "env-proj"

    @patch.dict(os.environ, {}, clear=True)
    def test_config_from_env_missing(self):
        assert mongodb_atlas_config_from_env() is None


class TestMongoDBAtlasValidation:
    @patch("app.integrations.mongodb_atlas._get_client")
    def test_validate_success(self, mock_get_client):
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"totalCount": 2}
        mock_resp.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_resp
        mock_get_client.return_value = mock_client

        config = MongoDBAtlasConfig(api_public_key="pub", api_private_key="priv", project_id="proj")
        result = validate_mongodb_atlas_config(config)

        assert result.ok is True
        assert "2 cluster(s)" in result.detail
        assert "proj" in result.detail
        mock_client.close.assert_called_once()

    @patch("app.integrations.mongodb_atlas._get_client", side_effect=Exception("Network error"))
    def test_validate_exception(self, _):
        config = MongoDBAtlasConfig(api_public_key="pub", api_private_key="priv", project_id="proj")
        result = validate_mongodb_atlas_config(config)
        assert result.ok is False
        assert "Network error" in result.detail

    def test_validate_not_configured(self):
        config = MongoDBAtlasConfig(api_public_key="", api_private_key="", project_id="")
        result = validate_mongodb_atlas_config(config)
        assert result.ok is False
        assert "required" in result.detail


class TestResolveIntegrations:
    def test_classify_mongodb_atlas(self):
        integrations = [
            {
                "id": "123",
                "service": "mongodb_atlas",
                "status": "active",
                "credentials": {
                    "api_public_key": "pub",
                    "api_private_key": "priv",
                    "project_id": "proj",
                },
            }
        ]
        resolved = _classify_integrations(integrations)
        assert "mongodb_atlas" in resolved
        assert resolved["mongodb_atlas"]["api_public_key"] == "pub"
        assert resolved["mongodb_atlas"]["project_id"] == "proj"

    def test_classify_atlas_alias(self):
        integrations = [
            {
                "id": "456",
                "service": "atlas",
                "status": "active",
                "credentials": {
                    "api_public_key": "pub",
                    "api_private_key": "priv",
                    "project_id": "proj",
                },
            }
        ]
        resolved = _classify_integrations(integrations)
        assert "mongodb_atlas" in resolved

    def test_classify_atlas_skipped_without_keys(self):
        integrations = [
            {
                "id": "789",
                "service": "mongodb_atlas",
                "status": "active",
                "credentials": {
                    "api_public_key": "",
                    "api_private_key": "",
                    "project_id": "proj",
                },
            }
        ]
        resolved = _classify_integrations(integrations)
        assert "mongodb_atlas" not in resolved
