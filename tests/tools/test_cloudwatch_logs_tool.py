"""Tests for CloudWatchLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.CloudWatchLogsTool import get_cloudwatch_logs
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestCloudWatchLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_cloudwatch_logs.__opensre_registered_tool__


def test_is_available_requires_log_group() -> None:
    rt = get_cloudwatch_logs.__opensre_registered_tool__
    assert rt.is_available({"cloudwatch": {"log_group": "/aws/lambda/fn"}}) is True
    assert rt.is_available({"cloudwatch": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_cloudwatch_logs.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["log_group"] == "/aws/lambda/my-function"
    assert params["log_stream"] == "2024/01/01/[$LATEST]abc123"
    assert params["filter_pattern"] == "req-123"
    assert params["limit"] == 100


def test_run_returns_error_when_no_log_group() -> None:
    result = get_cloudwatch_logs(log_group="")
    assert "error" in result


def test_run_with_filter_pattern_happy_path() -> None:
    mock_client = MagicMock()
    mock_client.filter_log_events.return_value = {
        "events": [{"message": "Error: something failed", "timestamp": 1000}]
    }
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group", filter_pattern="Error")
    assert result["found"] is True
    assert result["event_count"] == 1
    assert "Error: something failed" in result["error_logs"]


def test_run_with_filter_pattern_no_events() -> None:
    mock_client = MagicMock()
    mock_client.filter_log_events.return_value = {"events": []}
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group", filter_pattern="Error")
    assert result["found"] is False
    assert "filter_pattern" in result


def test_run_auto_discovers_log_stream() -> None:
    mock_client = MagicMock()
    mock_client.describe_log_streams.return_value = {
        "logStreams": [{"logStreamName": "stream-1"}]
    }
    mock_client.get_log_events.return_value = {
        "events": [{"message": "hello"}]
    }
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group")
    assert result["found"] is True
    assert result["log_stream"] == "stream-1"


def test_run_no_streams_found() -> None:
    mock_client = MagicMock()
    mock_client.describe_log_streams.return_value = {"logStreams": []}
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group")
    assert result["found"] is False


def test_run_with_explicit_log_stream() -> None:
    mock_client = MagicMock()
    mock_client.get_log_events.return_value = {
        "events": [{"message": "msg1"}, {"message": "msg2"}]
    }
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.return_value = mock_client
        result = get_cloudwatch_logs(log_group="/my/group", log_stream="stream-x")
    assert result["found"] is True
    assert result["event_count"] == 2


def test_run_handles_boto3_exception() -> None:
    with patch("app.tools.CloudWatchLogsTool.boto3") as mock_boto3:
        mock_boto3.client.side_effect = Exception("AWS error")
        result = get_cloudwatch_logs(log_group="/my/group")
    assert "error" in result
