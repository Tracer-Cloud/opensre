"""
Integration test using the example fixture.

Tests that run_investigation produces valid output with structural invariants.
"""

import json
from pathlib import Path

import pytest

# Initialize runtime first
import sys
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from config import init_runtime
init_runtime()

from src.models.alert import GrafanaAlertPayload, normalize_grafana_alert
from src.agent.graph import run_investigation

# Path to fixture
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "grafana_alert.json"


def load_sample_alert() -> GrafanaAlertPayload:
    """Load the sample Grafana alert from test fixtures."""
    with open(FIXTURE_PATH) as f:
        data = json.load(f)
    return GrafanaAlertPayload(**data)


@pytest.mark.integration
def test_investigation_produces_valid_output():
    """
    Load the example fixture and run a full investigation.
    
    Asserts structural invariants on the output.
    """
    # Load fixture
    grafana_payload = load_sample_alert()
    alert = normalize_grafana_alert(grafana_payload)
    
    # Run investigation
    final_state = run_investigation(
        alert_name=alert.alert_name,
        affected_table=alert.affected_table or "events_fact",
        severity=alert.severity,
    )
    
    # Assert structural invariants
    assert "slack_message" in final_state, "Missing slack_message in output"
    assert "problem_md" in final_state, "Missing problem_md in output"
    assert "root_cause" in final_state, "Missing root_cause in output"
    assert "confidence" in final_state, "Missing confidence in output"
    
    # Assert non-empty values
    assert final_state["slack_message"], "slack_message is empty"
    assert len(final_state["slack_message"]) > 100, "slack_message too short"
    
    assert final_state["problem_md"], "problem_md is empty"
    assert len(final_state["problem_md"]) > 100, "problem_md too short"
    
    assert final_state["root_cause"], "root_cause is empty"
    
    # Assert confidence is a valid float between 0 and 1
    confidence = final_state["confidence"]
    assert isinstance(confidence, (int, float)), "confidence must be numeric"
    assert 0.0 <= confidence <= 1.0, f"confidence {confidence} not in [0, 1]"


@pytest.mark.integration
def test_investigation_slack_message_structure():
    """
    Verify the slack_message contains expected sections.
    """
    grafana_payload = load_sample_alert()
    alert = normalize_grafana_alert(grafana_payload)
    
    final_state = run_investigation(
        alert_name=alert.alert_name,
        affected_table=alert.affected_table or "events_fact",
        severity=alert.severity,
    )
    
    slack_message = final_state["slack_message"]
    
    # Check for expected sections (case-insensitive)
    slack_lower = slack_message.lower()
    
    assert "conclusion" in slack_lower or "summary" in slack_lower, (
        "slack_message missing conclusion/summary section"
    )
    assert "confidence" in slack_lower, "slack_message missing confidence"
    assert "tracer" in slack_lower or "evidence" in slack_lower, (
        "slack_message missing evidence/tracer section"
    )


@pytest.mark.integration
def test_investigation_problem_md_structure():
    """
    Verify problem_md contains valid markdown.
    """
    grafana_payload = load_sample_alert()
    alert = normalize_grafana_alert(grafana_payload)
    
    final_state = run_investigation(
        alert_name=alert.alert_name,
        affected_table=alert.affected_table or "events_fact",
        severity=alert.severity,
    )
    
    problem_md = final_state["problem_md"]
    
    # Check for markdown structure (headers)
    assert "#" in problem_md, "problem_md should contain markdown headers"

