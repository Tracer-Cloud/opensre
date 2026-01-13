"""Tests for Pydantic models."""

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from src.models.alert import (
    Alert,
    GrafanaAlertPayload,
    normalize_grafana_alert,
)
from src.models.hypothesis import (
    Hypothesis,
    HypothesisStatus,
    Evidence,
    HYPOTHESIS_TEMPLATES,
    create_hypothesis_from_template,
)
from src.models.report import IncidentReport, RecommendedAction


class TestAlertModels:
    """Test alert normalization."""
    
    def test_normalize_grafana_alert(self):
        """Test that a Grafana payload is correctly normalized."""
        fixture_path = Path(__file__).parent.parent / "fixtures" / "grafana_alert.json"
        with open(fixture_path) as f:
            data = json.load(f)
        
        payload = GrafanaAlertPayload(**data)
        alert = normalize_grafana_alert(payload)
        
        assert alert.alert_name == "DataFreshnessSLABreach"
        assert alert.severity == "critical"
        assert alert.affected_table == "events_fact"
        assert alert.environment == "production"
        assert alert.source == "grafana"
    
    def test_alert_model_validation(self):
        """Test Alert model field validation."""
        alert = Alert(
            incident_id="test-123",
            alert_name="TestAlert",
            severity="warning",
            summary="Test summary",
            detected_at=datetime.now(timezone.utc),
        )
        
        assert alert.incident_id == "test-123"
        assert alert.environment == "production"  # default


class TestHypothesisModels:
    """Test hypothesis tracking models."""
    
    def test_hypothesis_creation(self):
        """Test creating a hypothesis."""
        hypothesis = Hypothesis(
            id="h1",
            name="Test Hypothesis",
            description="Testing something",
            evidence_needed=["Check X", "Check Y"],
            tools_to_use=["tool_x", "tool_y"],
        )
        
        assert hypothesis.status == HypothesisStatus.PENDING
        assert hypothesis.confidence == 0.0
        assert len(hypothesis.evidence) == 0
    
    def test_hypothesis_from_template(self):
        """Test creating hypothesis from template."""
        template = HYPOTHESIS_TEMPLATES[0]
        hypothesis = create_hypothesis_from_template(template)
        
        assert hypothesis.id == template["id"]
        assert hypothesis.name == template["name"]
        assert hypothesis.status == HypothesisStatus.PENDING
    
    def test_evidence_model(self):
        """Test evidence model."""
        evidence = Evidence(
            source="s3",
            tool_used="list_s3_files",
            finding="Found 2 files",
            supports_hypothesis=True,
            raw_data={"count": 2},
        )
        
        assert evidence.source == "s3"
        assert evidence.supports_hypothesis is True


class TestReportModels:
    """Test report generation models."""
    
    def test_incident_report_creation(self):
        """Test creating an incident report."""
        report = IncidentReport(
            incident_id="inc-123",
            alert_name="TestAlert",
            severity="critical",
            title="Test Incident",
            summary="Something went wrong",
            root_cause="The thing broke",
            root_cause_confidence=0.9,
            affected_systems=["system-a"],
            impact_description="Bad stuff happened",
            detected_at=datetime.now(timezone.utc),
        )
        
        assert report.incident_id == "inc-123"
        assert report.root_cause_confidence == 0.9
        assert len(report.recommended_actions) == 0
    
    def test_recommended_action(self):
        """Test recommended action model."""
        action = RecommendedAction(
            action="Fix the thing",
            priority="high",
            automated=True,
        )
        
        assert action.priority == "high"
        assert action.automated is True

