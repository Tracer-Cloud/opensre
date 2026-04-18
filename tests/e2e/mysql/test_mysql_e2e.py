"""MySQL E2E tests verifying integration with investigation pipeline.

Tests:
- MySQL config resolution from store and env
- MySQL verification (connection, server info)
- MySQL source detection in investigation state
- MySQL tools availability for query execution
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.integrations.verify import verify_integrations
from app.nodes.plan_actions.detect_sources import detect_sources
from app.nodes.resolve_integrations.node import _classify_integrations


class TestMySQLIntegrationResolution:
    """Test MySQL config resolution from multiple sources."""

    def test_mysql_resolution_from_store(self):
        """MySQL integration correctly resolved from local store."""
        integrations = [
            {
                "id": "mysql-prod",
                "service": "mysql",
                "status": "active",
                "credentials": {
                    "host": "prod-primary.mysql.net",
                    "port": 3306,
                    "database": "application_db",
                    "username": "app_user",
                    "password": "secure_password",
                    "ssl_mode": "required",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert "mysql" in resolved
        assert resolved["mysql"]["host"] == "prod-primary.mysql.net"
        assert resolved["mysql"]["port"] == 3306
        assert resolved["mysql"]["database"] == "application_db"
        assert resolved["mysql"]["username"] == "app_user"
        assert resolved["mysql"]["password"] == "secure_password"
        assert resolved["mysql"]["ssl_mode"] == "required"

    def test_mysql_invalid_config_skipped(self):
        """Invalid MySQL integration config is safely skipped."""
        integrations = [
            {
                "id": "bad-mysql",
                "service": "mysql",
                "status": "active",
                "credentials": {
                    "host": "",
                    "database": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert resolved.get("mysql") is None

    def test_mysql_missing_database_skipped(self):
        """MySQL integration without database is safely skipped."""
        integrations = [
            {
                "id": "no-db-mysql",
                "service": "mysql",
                "status": "active",
                "credentials": {
                    "host": "localhost",
                    "database": "",
                },
            }
        ]
        resolved = _classify_integrations(integrations)

        assert resolved.get("mysql") is None


class TestMySQLSourceDetection:
    """Test MySQL source detection in investigation context."""

    def test_detect_mysql_source_from_alert(self):
        """MySQL source detected from alert annotations."""
        raw_alert = {
            "commonAnnotations": {
                "mysql_database": "application_db",
                "mysql_table": "events",
            }
        }
        resolved_integrations = {
            "mysql": {
                "host": "localhost",
                "port": 3306,
                "database": "application_db",
                "username": "root",
                "password": "test123",
                "ssl_mode": "preferred",
            }
        }

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "mysql" in sources
        assert sources["mysql"]["host"] == "localhost"
        assert sources["mysql"]["database"] == "application_db"
        assert sources["mysql"]["table"] == "events"

    def test_detect_mysql_source_fallback_to_config(self):
        """MySQL source falls back to configured database if not in alert."""
        raw_alert = {
            "commonAnnotations": {},
        }
        resolved_integrations = {
            "mysql": {
                "host": "localhost",
                "port": 3306,
                "database": "default_db",
                "username": "root",
                "password": "test123",
                "ssl_mode": "preferred",
            }
        }

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "mysql" in sources
        assert sources["mysql"]["database"] == "default_db"

    def test_mysql_source_not_detected_if_unconfigured(self):
        """MySQL source is not included if not configured."""
        raw_alert = {"commonAnnotations": {}}
        resolved_integrations = {}

        sources = detect_sources(raw_alert, {}, resolved_integrations)

        assert "mysql" not in sources


class TestMySQLVerification:
    """Test MySQL integration verification flow."""

    @patch("app.integrations.mysql._get_connection")
    def test_verify_mysql_success(self, mock_get_connection, monkeypatch):
        """MySQL verification succeeds with valid config."""
        monkeypatch.setenv("MYSQL_HOST", "localhost")
        monkeypatch.setenv("MYSQL_DATABASE", "testdb")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"VERSION()": "8.0.32"}
        mock_get_connection.return_value = mock_conn

        results = verify_integrations(service="mysql")

        assert len(results) >= 1
        mysql_result = next((r for r in results if r["service"] == "mysql"), None)
        assert mysql_result is not None
        assert mysql_result["status"] == "passed"
        mock_get_connection.assert_called_once()

    @patch("app.integrations.mysql._get_connection")
    def test_verify_integrations_structure(self, mock_get_connection, monkeypatch):
        """Verify integrations returns a list with expected result fields."""
        monkeypatch.setenv("MYSQL_HOST", "localhost")
        monkeypatch.setenv("MYSQL_DATABASE", "testdb")

        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchone.return_value = {"VERSION()": "8.0.32"}
        mock_get_connection.return_value = mock_conn

        results = verify_integrations(service="mysql")
        assert isinstance(results, list)
        for result in results:
            if result["service"] == "mysql":
                assert "status" in result
                assert "detail" in result
                assert result["status"] in ("passed", "missing", "failed")


class TestMySQLToolsAvailability:
    """Test MySQL tools are available and configured."""

    def test_mysql_tools_exist_as_modules(self):
        """MySQL tools modules exist and are properly structured."""
        try:
            from app.tools import (
                MySQLCurrentProcessesTool,
                MySQLReplicationStatusTool,
                MySQLServerStatusTool,
                MySQLSlowQueriesTool,
                MySQLTableStatsTool,
            )

            assert MySQLServerStatusTool is not None
            assert MySQLCurrentProcessesTool is not None
            assert MySQLReplicationStatusTool is not None
            assert MySQLSlowQueriesTool is not None
            assert MySQLTableStatsTool is not None
        except ImportError as e:
            pytest.fail(f"Failed to import MySQL tool modules: {e}")


class TestMySQLAlertFixture:
    """Test the MySQL alert fixture is valid and parseable."""

    def test_mysql_alert_fixture_is_valid_json(self):
        """MySQL alert fixture is valid JSON."""
        fixture_path = Path(__file__).parent / "mysql_alert.json"
        assert fixture_path.exists(), f"Alert fixture not found at {fixture_path}"

        with fixture_path.open() as f:
            alert = json.load(f)

        assert isinstance(alert, dict)
        assert "state" in alert
        assert "commonLabels" in alert
        assert "commonAnnotations" in alert

    def test_mysql_alert_fixture_has_mysql_context(self):
        """MySQL alert fixture contains MySQL-specific context."""
        fixture_path = Path(__file__).parent / "mysql_alert.json"

        with fixture_path.open() as f:
            alert = json.load(f)

        labels = alert.get("commonLabels", {})
        annotations = alert.get("commonAnnotations", {})

        assert "mysql_instance" in labels
        assert "mysql_database" in annotations
        assert "mysql_table" in annotations


class TestMySQLIntegrationConfig:
    """Test MySQLIntegrationConfig model from app.integrations.models."""

    def test_mysql_integration_config_has_required_fields(self):
        """MySQL integration provides required fields in resolved config."""
        from app.integrations.models import MySQLIntegrationConfig

        config = MySQLIntegrationConfig(
            host="localhost",
            port=3306,
            database="test_db",
            username="root",
            password="test123",
            ssl_mode="preferred",
            integration_id="test-id",
        )

        assert config.host == "localhost"
        assert config.port == 3306
        assert config.database == "test_db"
        assert config.username == "root"
        assert config.password == "test123"
        assert config.ssl_mode == "preferred"
        assert config.integration_id == "test-id"
