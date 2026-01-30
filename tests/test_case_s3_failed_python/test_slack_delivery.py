"""
Test Slack delivery for S3 failed python demo.

Isolates the Slack sending functionality to verify it works correctly.
"""

import os
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from app.main import _run
from tests.test_case_s3_failed_python import use_case
from tests.test_case_s3_failed_python.test_orchestrator import _build_alert_annotations
from tests.utils.alert_factory import create_alert
from tests.utils.file_logger import configure_file_logging


def test_slack_delivery_with_s3_alert() -> None:
    """Test that Slack delivery is triggered when running investigation."""
    configure_file_logging("test_production.log")
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    # Run the use case to get failure results
    result = use_case.main(log_file="test_production.log")
    pipeline_name = result["pipeline_name"]

    # Skip if pipeline succeeded (unlikely but handle gracefully)
    if result["status"] == "success":
        pytest.skip("Pipeline succeeded - no alert to send")

    # Build alert exactly as orchestrator does
    raw_alert = create_alert(
        pipeline_name=pipeline_name,
        run_name=run_id,
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        annotations=_build_alert_annotations(result),
    )

    # Set TRACER_API_URL so Slack delivery doesn't skip
    # Mock Slack delivery where it's imported and used
    with (
        patch.dict(os.environ, {"TRACER_API_URL": "https://test.example.com"}),
        patch("app.main.send_slack_report") as mock_slack,
    ):
        # Run investigation (this should trigger Slack delivery)
        investigation_result = _run(
            alert_name=f"Pipeline failure: {pipeline_name}",
            pipeline_name=pipeline_name,
            severity="critical",
            raw_alert=raw_alert,
        )

        # Verify investigation completed
        assert "slack_message" in investigation_result
        assert investigation_result["slack_message"]
        assert "root_cause" in investigation_result

        # Verify Slack delivery was called with the message
        mock_slack.assert_called_once()
        call_args = mock_slack.call_args[0]
        assert len(call_args) == 1
        slack_message = call_args[0]
        assert slack_message == investigation_result["slack_message"]
        assert pipeline_name in slack_message


def test_slack_message_contains_expected_content() -> None:
    """Test that the Slack message contains expected RCA content."""
    configure_file_logging("test_production.log")
    run_id = f"run_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S')}"

    result = use_case.main(log_file="test_production.log")
    pipeline_name = result["pipeline_name"]

    if result["status"] == "success":
        pytest.skip("Pipeline succeeded - no alert to send")

    raw_alert = create_alert(
        pipeline_name=pipeline_name,
        run_name=run_id,
        status="failed",
        timestamp=datetime.now(UTC).isoformat(),
        annotations=_build_alert_annotations(result),
    )

    investigation_result = _run(
        alert_name=f"Pipeline failure: {pipeline_name}",
        pipeline_name=pipeline_name,
        severity="critical",
        raw_alert=raw_alert,
    )

    slack_message = investigation_result["slack_message"]

    # Verify message contains expected sections
    assert "*Conclusion*" in slack_message
    assert pipeline_name in slack_message
    assert "Confidence:" in slack_message or "*Confidence*" in slack_message
    assert "Validity Score:" in slack_message or "*Validity Score*" in slack_message
