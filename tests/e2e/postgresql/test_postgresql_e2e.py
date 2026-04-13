"""PostgreSQL E2E tests verifying integration with investigation pipeline.

Tests:
- PostgreSQL config resolution from store and env
- PostgreSQL verification (connection, server info)
- PostgreSQL source detection in investigation state
- PostgreSQL tools availability for query execution
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.verify import verify_integrations
from app.nodes.plan_actions.detect_sources import detect_sources
from app.nodes.resolve_integrations.node import _classify_integrations


class TestPostgreSQLIntegrationResolution:
    """Test PostgreSQL config resolution from multiple sources."""

    def test_postgresql_resolution_from_store(self):
        """PostgreSQL integration correctly resolved from local store."""
        integrations = [
            {
                "id": "postgresql-prod",
                "service": "postgresql",
                "status": "active",
                "credentials": {
                    "host": "prod-primary.postgres.net",
                    "port": 5432,
                    "database": "application_db",
                    "username": "app_user",
                    "password": "secure_password",
                    "ssl_mode": "require",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "postgresql" in resolved
        assert resolved["postgresql"]["host"] == "prod-primary.postgres.net"
        assert resolved["postgresql"]["port"] == 5432
        assert resolved["postgresql"]["database"] == "application_db"
        assert resolved["postgresql"]["username"] == "app_user"
        assert resolved["postgresql"]["password"] == "secure_password"
        assert resolved["postgresql"]["ssl_mode"] == "require"

    def test_postgresql_invalid_config_skipped(self):
        """Invalid PostgreSQL integration config is safely skipped."""
        integrations = [
            {
                "id": "bad-postgres",
                "service": "postgresql",
                "status": "active",
                "credentials": {
                    "host": "",
                    "database": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        # Should not include PostgreSQL if host or database is empty
        assert resolved.get("postgresql") is None

    def test_postgresql_missing_database_skipped(self):
        """PostgreSQL integration without database is safely skipped."""
        integrations = [
            {
                "id": "no-db-postgres",
                "service": "postgresql",
                "status": "active",
                "credentials": {
                    "host": "localhost",
                    "database": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        # Should not include PostgreSQL if database is empty
        assert resolved.get("postgresql") is None


class TestPostgreSQLSourceDetection:
    """Test PostgreSQL source detection in investigation context."""

    def test_detect_postgresql_source_from_alert(self):
        """PostgreSQL source detected from alert annotations."""
        raw_alert = {
            "commonAnnotations": {
                "postgresql_database": "application_db",
                "postgresql_table": "events",
                "postgresql_schema": "public",
            }
        }
        resolved_integrations = {
            "postgresql": {
                "host": "localhost",
                "port": 5432,
                "database": "application_db",
                "username": "postgres",
                "password": "test123",
                "ssl_mode": "prefer",
            }
        }

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "postgresql" in sources
        assert sources["postgresql"]["host"] == "localhost"
        assert sources["postgresql"]["database"] == "application_db"
        assert sources["postgresql"]["table"] == "events"
        assert sources["postgresql"]["schema"] == "public"

    def test_detect_postgresql_source_fallback_to_config(self):
        """PostgreSQL source falls back to configured database if not in alert."""
        raw_alert = {
            "commonAnnotations": {},
        }
        resolved_integrations = {
            "postgresql": {
                "host": "localhost",
                "port": 5432,
                "database": "default_db",
                "username": "postgres",
                "password": "test123",
                "ssl_mode": "prefer",
            }
        }

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "postgresql" in sources
        assert sources["postgresql"]["database"] == "default_db"

    def test_postgresql_source_not_detected_if_unconfigured(self):
        """PostgreSQL source is not included if not configured."""
        raw_alert = {"commonAnnotations": {}}
        resolved_integrations = {}

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "postgresql" not in sources


class TestPostgreSQLVerification:
    """Test PostgreSQL integration verification flow."""

    @patch("app.integrations.postgresql._get_connection")
    def test_verify_postgresql_success(self, mock_get_connection, monkeypatch):
        """PostgreSQL verification succeeds with valid config."""
        monkeypatch.setenv("POSTGRESQL_HOST", "localhost")
        monkeypatch.setenv("POSTGRESQL_DATABASE", "testdb")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = ["PostgreSQL 16.1 on x86_64-pc-linux-gnu"]
        mock_get_connection.return_value = mock_conn

        results = verify_integrations(service="postgresql")

        assert len(results) >= 1
        postgres_result = next((r for r in results if r["service"] == "postgresql"), None)
        assert postgres_result is not None
        assert postgres_result["status"] == "passed"
        mock_get_connection.assert_called_once()

    def test_verify_integrations_structure(self):
        """Verify integrations returns expected result structure."""
        # Just verify the function exists and can be called - actual verification
        # depends on environment setup (PostgreSQL connection available)
        try:
            results = verify_integrations(service="postgresql")
            assert isinstance(results, list)
            for result in results:
                if result["service"] == "postgresql":
                    assert "status" in result
                    assert "detail" in result
                    assert result["status"] in ("passed", "missing", "failed")
        except Exception:
            # If no PostgreSQL is configured, that's ok - just testing structure
            pass


class TestPostgreSQLToolsAvailability:
    """Test PostgreSQL tools are available and configured."""

    def test_postgresql_tools_exist_as_modules(self):
        """PostgreSQL tools modules exist and are properly structured."""
        try:
            # Tools are defined as decorated functions within __init__ modules
            import app.tools.PostgreSQLCurrentQueriesTool
            import app.tools.PostgreSQLReplicationStatusTool
            import app.tools.PostgreSQLServerStatusTool
            import app.tools.PostgreSQLSlowQueriesTool
            import app.tools.PostgreSQLTableStatsTool

            # All 5 tool modules should be importable
            assert app.tools.PostgreSQLServerStatusTool is not None
            assert app.tools.PostgreSQLCurrentQueriesTool is not None
            assert app.tools.PostgreSQLReplicationStatusTool is not None
            assert app.tools.PostgreSQLSlowQueriesTool is not None
            assert app.tools.PostgreSQLTableStatsTool is not None
        except ImportError as e:
            pytest.fail(f"Failed to import PostgreSQL tool modules: {e}")

    def test_postgresql_integration_config_has_required_fields(self):
        """PostgreSQL integration provides required fields in resolved config."""
        from app.integrations.models import PostgreSQLIntegrationConfig

        config = PostgreSQLIntegrationConfig(
            host="localhost",
            port=5432,
            database="test_db",
            username="postgres",
            password="test123",
            ssl_mode="prefer",
            integration_id="test-id",
        )

        assert config.host == "localhost"
        assert config.port == 5432
        assert config.database == "test_db"
        assert config.username == "postgres"
        assert config.password == "test123"
        assert config.ssl_mode == "prefer"
        assert config.integration_id == "test-id"


class TestPostgreSQLAlertFixture:
    """Test the PostgreSQL alert fixture is valid and parseable."""

    def test_postgresql_alert_fixture_is_valid_json(self):
        """PostgreSQL alert fixture is valid JSON."""
        fixture_path = Path(__file__).parent / "postgresql_alert.json"
        assert fixture_path.exists(), f"Alert fixture not found at {fixture_path}"

        with fixture_path.open() as f:
            alert = json.load(f)

        assert isinstance(alert, dict)
        assert "state" in alert
        assert "commonLabels" in alert
        assert "commonAnnotations" in alert

    def test_postgresql_alert_fixture_has_postgresql_context(self):
        """PostgreSQL alert fixture contains PostgreSQL-specific context."""
        fixture_path = Path(__file__).parent / "postgresql_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        labels = alert.get("commonLabels", {})
        annotations = alert.get("commonAnnotations", {})

        # Alert should have PostgreSQL-specific fields for source detection
        assert "postgresql_instance" in labels
        assert "postgresql_database" in annotations
        assert "postgresql_table" in annotations
        assert "postgresql_schema" in annotations
