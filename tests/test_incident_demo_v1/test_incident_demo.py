"""
End-to-end test for the incident resolution agent.

Tests the full graph execution from alert to RCA report.
Owns persistence: writes output files to tests/test_incident_demo_v1/output/.
"""

import json
from pathlib import Path

import pytest

from config.config import init_runtime
from src.models.alert import GrafanaAlertPayload, normalize_grafana_alert
from src.agent.graph import run_investigation
from src.presentation.export import export_outputs


# ─────────────────────────────────────────────────────────────────────────────
# Test Paths
# ─────────────────────────────────────────────────────────────────────────────

TEST_DIR = Path(__file__).parent
FIXTURES_DIR = TEST_DIR / "fixtures"
OUTPUT_DIR = TEST_DIR / "output"


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def load_test_alert() -> GrafanaAlertPayload:
    """Load the test Grafana alert from test fixtures."""
    fixture_path = FIXTURES_DIR / "grafana_alert.json"
    with open(fixture_path) as f:
        data = json.load(f)
    return GrafanaAlertPayload(**data)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module", autouse=True)
def setup_runtime():
    """Initialize runtime configuration before tests."""
    init_runtime()


@pytest.fixture(scope="module")
def investigation_result():
    """
    Run investigation once and share result across tests in this module.

    This avoids running the expensive LLM calls multiple times.
    """
    grafana_payload = load_test_alert()
    alert = normalize_grafana_alert(grafana_payload)

    return run_investigation(
        alert_name=alert.alert_name,
        affected_table=alert.affected_table or "events_fact",
        severity=alert.severity,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────────────

class TestIncidentDemoE2E:
    """End-to-end tests for the incident resolution agent."""

    def test_investigation_produces_slack_message(self, investigation_result):
        """Test that running the investigation produces a non-empty slack message."""
        final_state = investigation_result

        # Assert slack_message is non-empty
        assert final_state["slack_message"], "slack_message should not be empty"
        assert len(final_state["slack_message"]) > 10, "slack_message should have meaningful content"

        # Assert content structure (contains key sections)
        assert "RCA" in final_state["slack_message"] or "root" in final_state["slack_message"].lower()

    def test_investigation_produces_problem_md(self, investigation_result):
        """Test that running the investigation produces a non-empty problem.md."""
        final_state = investigation_result

        # Assert problem_md is non-empty
        assert final_state["problem_md"], "problem_md should not be empty"
        assert len(final_state["problem_md"]) > 10, "problem_md should have meaningful content"

        # Assert markdown structure
        assert "#" in final_state["problem_md"], "problem_md should contain markdown headers"

    def test_investigation_full_output(self, investigation_result):
        """Test complete investigation with all expected outputs."""
        final_state = investigation_result

        # Assert all expected outputs are present
        assert final_state["slack_message"], "slack_message should not be empty"
        assert final_state["problem_md"], "problem_md should not be empty"
        assert final_state["root_cause"], "root_cause should be determined"
        assert final_state["confidence"] >= 0.0, "confidence should be non-negative"
        assert final_state["confidence"] <= 1.0, "confidence should not exceed 1.0"


class TestExportOutputs:
    """Tests for the export functionality."""

    def test_export_creates_files(self, investigation_result):
        """Test that export_outputs creates the expected files."""
        final_state = investigation_result

        # Export to test output directory
        result = export_outputs(final_state, OUTPUT_DIR)

        # Assert files were created
        assert result["problem_md_path"].exists(), "problem.md should be created"
        assert result["slack_message_path"].exists(), "slack_message.txt should be created"

        # Assert file content matches state
        assert result["problem_md_path"].read_text() == final_state["problem_md"]
        assert result["slack_message_path"].read_text() == final_state["slack_message"]

    def test_export_validates_required_keys(self):
        """Test that export_outputs raises KeyError for missing keys."""
        with pytest.raises(KeyError, match="problem_md"):
            export_outputs({}, OUTPUT_DIR)

        with pytest.raises(KeyError, match="slack_message"):
            export_outputs({"problem_md": "test"}, OUTPUT_DIR)
