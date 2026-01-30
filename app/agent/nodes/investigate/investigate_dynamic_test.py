"""Integration tests for dynamic investigation action filtering and execution."""

from unittest.mock import MagicMock, patch

from app.agent.nodes.investigate.data_sources import detect_available_sources
from app.agent.nodes.investigate.execute_actions import execute_actions
from app.agent.nodes.investigate.prompt import build_investigation_prompt
from app.agent.state import InvestigationState
from app.agent.tools.tool_actions.investigation_actions import get_available_actions


def test_action_filtering_by_availability_cloudwatch():
    """Test that only CloudWatch actions are available when CloudWatch sources are present."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "cloudwatch_log_group": "/aws/batch/job",
                "cloudwatch_log_stream": "job-12345/container-name/abc123",
            }
        },
        "context": {},
        "problem_md": "Pipeline failed",
    }

    available_sources = detect_available_sources(state)
    all_actions = get_available_actions()

    available_actions = [
        action
        for action in all_actions
        if action.availability_check is None or action.availability_check(available_sources)
    ]

    available_action_names = [action.name for action in available_actions]
    assert "get_cloudwatch_logs" in available_action_names
    assert "get_failed_jobs" not in available_action_names  # Requires trace_id
    assert "get_error_logs" not in available_action_names  # Requires trace_id


def test_action_filtering_by_availability_tracer_web():
    """Test that Tracer Web actions are available when trace_id is present."""
    state: InvestigationState = {
        "raw_alert": {},
        "context": {
            "tracer_web_run": {
                "trace_id": "a4b56a5c-03c5-438f-96b6-60f8db7c13d5",
            }
        },
        "problem_md": "Pipeline failed",
    }

    available_sources = detect_available_sources(state)
    all_actions = get_available_actions()

    available_actions = [
        action
        for action in all_actions
        if action.availability_check is None or action.availability_check(available_sources)
    ]

    available_action_names = [action.name for action in available_actions]
    assert "get_failed_jobs" in available_action_names
    assert "get_failed_tools" in available_action_names
    assert "get_error_logs" in available_action_names
    assert "get_cloudwatch_logs" not in available_action_names  # Requires CloudWatch sources


def test_action_filtering_multiple_sources():
    """Test action filtering with multiple sources available."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "cloudwatch_log_group": "/aws/batch/job",
                "cloudwatch_log_stream": "job-12345/container-name/abc123",
            }
        },
        "context": {
            "tracer_web_run": {
                "trace_id": "a4b56a5c-03c5-438f-96b6-60f8db7c13d5",
            }
        },
        "problem_md": "Pipeline failed",
    }

    available_sources = detect_available_sources(state)
    all_actions = get_available_actions()

    available_actions = [
        action
        for action in all_actions
        if action.availability_check is None or action.availability_check(available_sources)
    ]

    available_action_names = [action.name for action in available_actions]
    assert "get_cloudwatch_logs" in available_action_names
    assert "get_failed_jobs" in available_action_names
    assert "get_failed_tools" in available_action_names
    assert "get_error_logs" in available_action_names


def test_prompt_includes_available_sources_hint():
    """Test that prompt includes hints for available sources."""
    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "cloudwatch_log_group": "/aws/batch/job",
                "cloudwatch_log_stream": "job-12345/container-name/abc123",
                "s3_bucket": "my-data-bucket",
                "s3_prefix": "raw/events/",
            }
        },
        "context": {},
        "problem_md": "Pipeline failed",
    }

    available_sources = detect_available_sources(state)
    all_actions = get_available_actions()
    available_actions = [
        action
        for action in all_actions
        if action.availability_check is None or action.availability_check(available_sources)
    ]

    prompt = build_investigation_prompt(state, available_actions, available_sources)

    assert "CloudWatch Logs Available" in prompt
    assert "/aws/batch/job" in prompt
    assert "S3 Storage Available" in prompt
    assert "my-data-bucket" in prompt


@patch("app.agent.tools.tool_actions.cloudwatch_actions.boto3")
def test_execute_actions_with_parameter_extraction(mock_boto3):
    """Test that actions execute with parameters extracted from available sources."""
    mock_client = MagicMock()
    mock_boto3.client.return_value = mock_client
    mock_client.get_log_events.return_value = {
        "events": [
            {"message": "Error: File not found", "timestamp": 1234567890}
        ]
    }

    state: InvestigationState = {
        "raw_alert": {
            "annotations": {
                "cloudwatch_log_group": "/aws/batch/job",
                "cloudwatch_log_stream": "job-12345/container-name/abc123",
            }
        },
        "context": {},
    }

    available_sources = detect_available_sources(state)

    results = execute_actions(["get_cloudwatch_logs"], available_sources)

    assert "get_cloudwatch_logs" in results
    assert results["get_cloudwatch_logs"].success
    mock_client.get_log_events.assert_called_once_with(
        logGroupName="/aws/batch/job",
        logStreamName="job-12345/container-name/abc123",
        limit=100,
        startFromHead=False,
    )


def test_execute_actions_skips_unavailable_actions():
    """Test that unavailable actions are skipped during execution."""
    state: InvestigationState = {
        "raw_alert": {},
        "context": {},
    }

    available_sources = detect_available_sources(state)

    results = execute_actions(["get_cloudwatch_logs", "get_failed_jobs"], available_sources)

    assert "get_cloudwatch_logs" in results
    assert not results["get_cloudwatch_logs"].success
    assert "Action not available" in results["get_cloudwatch_logs"].error

    assert "get_failed_jobs" in results
    assert not results["get_failed_jobs"].success


def test_execute_actions_with_tracer_web_sources():
    """Test action execution with Tracer Web sources."""
    state: InvestigationState = {
        "raw_alert": {},
        "context": {
            "tracer_web_run": {
                "trace_id": "test-trace-id-123",
            }
        },
    }

    available_sources = detect_available_sources(state)

    with patch("app.agent.tools.tool_actions.tracer_jobs.get_tracer_web_client") as mock_client:
        mock_web_client = MagicMock()
        mock_client.return_value = mock_web_client
        mock_web_client.get_batch_jobs.return_value = {
            "data": [
                {"status": "FAILED", "jobName": "job-1", "statusReason": "Container failed"}
            ]
        }

        results = execute_actions(["get_failed_jobs"], available_sources)

        assert "get_failed_jobs" in results
        assert results["get_failed_jobs"].success
        mock_web_client.get_batch_jobs.assert_called_once_with(
            "test-trace-id-123", ["FAILED", "SUCCEEDED"], return_dict=True
        )
