"""Tests for report generation."""

from datetime import datetime, timezone
from pathlib import Path
import tempfile

import pytest

from src.models.report import IncidentReport, RecommendedAction, InvestigationTimeline
from src.models.hypothesis import Hypothesis, HypothesisStatus, Evidence
from src.agent.report_generator import (
    generate_report,
    generate_slack_message,
    generate_problem_md,
)


@pytest.fixture
def sample_report():
    """Create a sample incident report for testing."""
    hypothesis = Hypothesis(
        id="h3",
        name="Output marker missing",
        description="The _SUCCESS marker was not written",
        evidence_needed=["Check marker"],
        tools_to_use=["check_success_marker"],
        status=HypothesisStatus.CONFIRMED,
        confidence=0.9,
        evidence=[
            Evidence(
                source="s3",
                tool_used="check_success_marker",
                finding="_SUCCESS marker MISSING",
                supports_hypothesis=True,
            )
        ],
    )
    
    return IncidentReport(
        incident_id="abc123",
        alert_name="DataFreshnessSLABreach",
        severity="critical",
        title="DataFreshnessSLABreach: events_fact freshness breach",
        summary="Table events_fact has not been updated in over 2 hours",
        root_cause="The Nextflow finalize step failed due to S3 permission denied",
        root_cause_confidence=0.9,
        confirmed_hypothesis=hypothesis,
        rejected_hypotheses=["Nextflow transform failed", "Service B loader crashed"],
        evidence_summary=hypothesis.evidence,
        affected_systems=["events_fact", "Nextflow", "Service B Loader"],
        impact_description="events_fact table was stale",
        recommended_actions=[
            RecommendedAction(
                action="Rerun Nextflow finalize step",
                priority="high",
                automated=True,
            ),
            RecommendedAction(
                action="Fix IAM permissions",
                priority="critical",
                automated=False,
            ),
        ],
        detected_at=datetime(2026, 1, 13, 2, 13, 0, tzinfo=timezone.utc),
        investigation_timeline=InvestigationTimeline(
            started_at=datetime(2026, 1, 13, 2, 13, 10, tzinfo=timezone.utc),
            completed_at=datetime(2026, 1, 13, 2, 13, 25, tzinfo=timezone.utc),
            duration_seconds=15.0,
            steps_executed=10,
            hypotheses_tested=4,
        ),
    )


class TestReportGeneration:
    """Test report generation functions."""
    
    def test_generate_json_report(self, sample_report):
        """Test JSON report generation."""
        report = generate_report(sample_report)
        
        assert report["incident_id"] == "abc123"
        assert report["severity"] == "critical"
        assert report["root_cause"]["confidence"] == 0.9
        assert len(report["recommended_actions"]) == 2
        assert report["investigation"]["duration_seconds"] == 15.0
    
    def test_generate_slack_message(self, sample_report):
        """Test Slack message generation."""
        message = generate_slack_message(sample_report)
        
        assert "blocks" in message
        blocks = message["blocks"]
        
        # Should have header, sections, dividers
        assert any(b["type"] == "header" for b in blocks)
        assert any(b["type"] == "section" for b in blocks)
        
        # Should mention root cause
        section_texts = [b.get("text", {}).get("text", "") for b in blocks if b["type"] == "section"]
        assert any("Root Cause" in t for t in section_texts)
    
    def test_generate_problem_md(self, sample_report):
        """Test problem.md generation."""
        md_content = generate_problem_md(sample_report)
        
        assert "# Incident Report:" in md_content
        assert "events_fact" in md_content
        assert "Root Cause Analysis" in md_content
        assert "Recommended Actions" in md_content
        assert "Investigation Timeline" in md_content
    
    def test_generate_problem_md_to_file(self, sample_report):
        """Test writing problem.md to file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            output_path = Path(tmpdir) / "problem.md"
            generate_problem_md(sample_report, output_path)
            
            assert output_path.exists()
            content = output_path.read_text()
            assert "Incident Report" in content

