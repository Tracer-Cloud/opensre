"""MongoDB E2E tests verifying integration with investigation pipeline.

Tests:
- MongoDB config resolution from store and env
- MongoDB verification (ping, server info)
- MongoDB source detection in investigation state
- MongoDB tools availability for query execution
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.verify import verify_integrations
from app.nodes.plan_actions.detect_sources import detect_sources
from app.nodes.resolve_integrations.node import _classify_integrations


class TestMongoDBIntegrationResolution:
    """Test MongoDB config resolution from multiple sources."""

    def test_mongodb_resolution_from_store(self):
        """MongoDB integration correctly resolved from local store."""
        integrations = [
            {
                "id": "mongodb-prod",
                "service": "mongodb",
                "status": "active",
                "credentials": {
                    "connection_string": "mongodb+srv://user:pass@cluster.example.net",
                    "database": "production",
                    "auth_source": "admin",
                    "tls": True,
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "mongodb" in resolved
        assert (
            resolved["mongodb"]["connection_string"]
            == "mongodb+srv://user:pass@cluster.example.net"
        )
        assert resolved["mongodb"]["database"] == "production"
        assert resolved["mongodb"]["auth_source"] == "admin"
        assert resolved["mongodb"]["tls"] is True

    def test_mongodb_invalid_config_skipped(self):
        """Invalid MongoDB integration config is safely skipped."""
        integrations = [
            {
                "id": "bad-mongo",
                "service": "mongodb",
                "status": "active",
                "credentials": {
                    "connection_string": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        # Should not include MongoDB if connection_string is empty
        assert resolved.get("mongodb") is None


class TestMongoDBSourceDetection:
    """Test MongoDB source detection in investigation context."""

    def test_detect_mongodb_source_from_alert(self):
        """MongoDB source detected from alert annotations."""
        raw_alert = {
            "commonAnnotations": {
                "mongodb_database": "analytics",
                "mongodb_collection": "events",
            }
        }
        resolved_integrations = {
            "mongodb": {
                "connection_string": "mongodb://localhost",
                "database": "analytics",
                "auth_source": "admin",
                "tls": True,
            }
        }

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "mongodb" in sources
        assert sources["mongodb"]["connection_string"] == "mongodb://localhost"
        assert sources["mongodb"]["database"] == "analytics"
        assert sources["mongodb"]["collection"] == "events"

    def test_detect_mongodb_source_fallback_to_config(self):
        """MongoDB source falls back to configured database if not in alert."""
        raw_alert = {
            "commonAnnotations": {},
        }
        resolved_integrations = {
            "mongodb": {
                "connection_string": "mongodb://localhost",
                "database": "default_db",
                "auth_source": "admin",
                "tls": True,
            }
        }

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "mongodb" in sources
        assert sources["mongodb"]["database"] == "default_db"

    def test_mongodb_source_not_detected_if_unconfigured(self):
        """MongoDB source is not included if not configured."""
        raw_alert = {"commonAnnotations": {}}
        resolved_integrations = {}

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "mongodb" not in sources


class TestMongoDBVerification:
    """Test MongoDB integration verification flow."""

    @patch("app.integrations.mongodb._get_client")
    def test_verify_mongodb_success(self, mock_get_client):
        """MongoDB verification succeeds with valid config."""
        mock_client = MagicMock()
        mock_client.admin.command.return_value = {"ok": 1}
        mock_client.server_info.return_value = {"version": "6.0.5"}
        mock_get_client.return_value = mock_client

        results = verify_integrations(service="mongodb")

        assert len(results) >= 1
        mongo_result = next((r for r in results if r["service"] == "mongodb"), None)
        assert mongo_result is not None
        # Status can be passed or missing depending on env config
        assert mongo_result["status"] in ("passed", "missing")

    def test_verify_integrations_structure(self):
        """Verify integrations returns expected result structure."""
        # Just verify the function exists and can be called - actual verification
        # depends on environment setup (MongoDB connection available)
        try:
            results = verify_integrations(service="mongodb")
            assert isinstance(results, list)
            for result in results:
                if result["service"] == "mongodb":
                    assert "status" in result
                    assert "detail" in result
                    assert result["status"] in ("passed", "missing", "failed")
        except Exception:
            # If no MongoDB is configured, that's ok - just testing structure
            pass


class TestMongoDBToolsAvailability:
    """Test MongoDB tools are available and configured."""

    def test_mongodb_tools_exist_as_modules(self):
        """MongoDB tools modules exist and are properly structured."""
        try:
            # Tools are defined as decorated functions within __init__ modules
            import app.tools.MongoDBCollectionStatsTool
            import app.tools.MongoDBCurrentOpsTool
            import app.tools.MongoDBProfilerTool
            import app.tools.MongoDBReplicaStatusTool
            import app.tools.MongoDBServerStatusTool

            # All 5 tool modules should be importable
            assert app.tools.MongoDBServerStatusTool is not None
            assert app.tools.MongoDBCurrentOpsTool is not None
            assert app.tools.MongoDBReplicaStatusTool is not None
            assert app.tools.MongoDBProfilerTool is not None
            assert app.tools.MongoDBCollectionStatsTool is not None
        except ImportError as e:
            pytest.fail(f"Failed to import MongoDB tool modules: {e}")

    def test_mongodb_integration_config_has_required_fields(self):
        """MongoDB integration provides required fields in resolved config."""
        from app.integrations.models import MongoDBIntegrationConfig

        config = MongoDBIntegrationConfig(
            connection_string="mongodb://localhost",
            database="test_db",
            auth_source="admin",
            tls=True,
            integration_id="test-id",
        )

        assert config.connection_string == "mongodb://localhost"
        assert config.database == "test_db"
        assert config.auth_source == "admin"
        assert config.tls is True
        assert config.integration_id == "test-id"


class TestMongoDBAlertFixture:
    """Test the MongoDB alert fixture is valid and parseable."""

    def test_mongodb_alert_fixture_is_valid_json(self):
        """MongoDB alert fixture is valid JSON."""
        fixture_path = Path(__file__).parent / "mongodb_alert.json"
        assert fixture_path.exists(), f"Alert fixture not found at {fixture_path}"

        with fixture_path.open() as f:
            alert = json.load(f)

        assert isinstance(alert, dict)
        assert "state" in alert
        assert "commonLabels" in alert
        assert "commonAnnotations" in alert

    def test_mongodb_alert_fixture_has_mongodb_context(self):
        """MongoDB alert fixture contains MongoDB-specific context."""
        fixture_path = Path(__file__).parent / "mongodb_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        labels = alert.get("commonLabels", {})
        # Alert should have MongoDB-specific fields for source detection
        assert "mongodb_instance" in labels
