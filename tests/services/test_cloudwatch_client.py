"""Direct unit tests for app/services/cloudwatch_client.py service functions.

Tests cover get_metric_statistics, filter_log_events, and get_log_events
with mocked boto3 client and credential helpers. Tool-registration
assertions are intentionally excluded.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError

from app.services.cloudwatch_client import (
    filter_log_events,
    get_log_events,
    get_metric_statistics,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _mock_creds():
    with patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None):
        yield


@pytest.fixture()
def mock_cw_client():
    client = MagicMock()
    with patch("app.services.cloudwatch_client._get_cloudwatch_client", return_value=client):
        yield client


@pytest.fixture()
def mock_logs_client():
    client = MagicMock()
    with patch("app.services.cloudwatch_client._get_cloudwatch_logs_client", return_value=client):
        yield client


# ---------------------------------------------------------------------------
# get_metric_statistics
# ---------------------------------------------------------------------------


class TestGetMetricStatistics:
    def test_returns_data_on_success(self, mock_cw_client):
        mock_cw_client.get_metric_statistics.return_value = {"Datapoints": [{"Average": 50.0}]}
        result = get_metric_statistics(
            namespace="AWS/Batch",
            metric_name="CPUUtilization",
            start_time="2024-01-01T00:00:00Z",
            end_time="2024-01-01T01:00:00Z",
        )
        assert result["success"] is True
        assert result["data"]["Datapoints"] == [{"Average": 50.0}]

    def test_passes_dimensions_and_statistics(self, mock_cw_client):
        mock_cw_client.get_metric_statistics.return_value = {"Datapoints": []}
        get_metric_statistics(
            namespace="AWS/ECS",
            metric_name="MemoryUtilization",
            dimensions=[{"Name": "ServiceName", "Value": "my-service"}],
            start_time="2024-01-01T00:00:00Z",
            end_time="2024-01-01T01:00:00Z",
            statistics=["Average", "Sum"],
        )
        call_kwargs = mock_cw_client.get_metric_statistics.call_args.kwargs
        assert call_kwargs["Dimensions"] == [{"Name": "ServiceName", "Value": "my-service"}]
        assert call_kwargs["Statistics"] == ["Average", "Sum"]

    def test_default_statistics_when_none(self, mock_cw_client):
        mock_cw_client.get_metric_statistics.return_value = {"Datapoints": []}
        get_metric_statistics(
            namespace="AWS/Batch",
            metric_name="CPUUtilization",
            start_time="2024-01-01T00:00:00Z",
            end_time="2024-01-01T01:00:00Z",
        )
        call_kwargs = mock_cw_client.get_metric_statistics.call_args.kwargs
        assert call_kwargs["Statistics"] == ["Average", "Maximum", "Minimum"]

    def test_returns_error_on_client_error(self, mock_cw_client):
        mock_cw_client.get_metric_statistics.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Not authorized"}}, "GetMetricStatistics"
        )
        result = get_metric_statistics(
            namespace="AWS/Batch",
            metric_name="CPUUtilization",
            start_time="2024-01-01T00:00:00Z",
            end_time="2024-01-01T01:00:00Z",
        )
        assert result["success"] is False
        assert "AccessDenied" in result["error"]

    def test_returns_error_when_no_client(self):
        with patch("app.services.cloudwatch_client._get_cloudwatch_client", return_value=None):
            result = get_metric_statistics(
                namespace="AWS/Batch",
                metric_name="CPUUtilization",
                start_time="2024-01-01T00:00:00Z",
                end_time="2024-01-01T01:00:00Z",
            )
        assert result["success"] is False
        assert "boto3 not available" in result["error"]

    def test_returns_error_when_credentials_missing(self):
        with patch(
            "app.services.cloudwatch_client.require_aws_credentials",
            return_value={"success": False, "error": "Missing AWS credentials"},
        ):
            result = get_metric_statistics(
                namespace="AWS/Batch",
                metric_name="CPUUtilization",
                start_time="2024-01-01T00:00:00Z",
                end_time="2024-01-01T01:00:00Z",
            )
        assert result["success"] is False


# ---------------------------------------------------------------------------
# filter_log_events
# ---------------------------------------------------------------------------


class TestFilterLogEvents:
    def test_returns_events_on_success(self, mock_logs_client):
        mock_logs_client.filter_log_events.return_value = {
            "events": [{"message": "Error occurred", "timestamp": 1000}]
        }
        result = filter_log_events(log_group_name="/aws/batch/job", filter_pattern="Error")
        assert result["success"] is True
        assert len(result["data"]) == 1

    def test_passes_optional_params(self, mock_logs_client):
        mock_logs_client.filter_log_events.return_value = {"events": []}
        filter_log_events(
            log_group_name="/aws/lambda/fn",
            filter_pattern="timeout",
            start_time=1700000000000,
            end_time=1700003600000,
            limit=50,
        )
        call_kwargs = mock_logs_client.filter_log_events.call_args.kwargs
        assert call_kwargs["filterPattern"] == "timeout"
        assert call_kwargs["startTime"] == 1700000000000
        assert call_kwargs["endTime"] == 1700003600000
        assert call_kwargs["limit"] == 50

    def test_omits_optional_params_when_none(self, mock_logs_client):
        mock_logs_client.filter_log_events.return_value = {"events": []}
        filter_log_events(log_group_name="/aws/batch/job")
        call_kwargs = mock_logs_client.filter_log_events.call_args.kwargs
        assert "filterPattern" not in call_kwargs
        assert "startTime" not in call_kwargs
        assert "endTime" not in call_kwargs

    def test_returns_error_on_client_error(self, mock_logs_client):
        mock_logs_client.filter_log_events.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Log group not found"}},
            "FilterLogEvents",
        )
        result = filter_log_events(log_group_name="/nonexistent")
        assert result["success"] is False
        assert "ResourceNotFoundException" in result["error"]

    def test_returns_error_when_no_client(self):
        with patch("app.services.cloudwatch_client._get_cloudwatch_logs_client", return_value=None):
            result = filter_log_events(log_group_name="/aws/batch/job")
        assert result["success"] is False
        assert "boto3 not available" in result["error"]

    def test_returns_error_when_credentials_missing(self):
        with patch(
            "app.services.cloudwatch_client.require_aws_credentials",
            return_value={"success": False, "error": "Missing AWS credentials"},
        ):
            result = filter_log_events(log_group_name="/aws/batch/job")
        assert result["success"] is False


# ---------------------------------------------------------------------------
# get_log_events
# ---------------------------------------------------------------------------


class TestGetLogEvents:
    def test_returns_events_on_success(self, mock_logs_client):
        mock_logs_client.get_log_events.return_value = {
            "events": [{"message": "line 1"}, {"message": "line 2"}]
        }
        result = get_log_events(
            log_group_name="/aws/batch/job",
            log_stream_name="stream-1",
        )
        assert result["success"] is True
        assert len(result["data"]) == 2

    def test_passes_optional_params(self, mock_logs_client):
        mock_logs_client.get_log_events.return_value = {"events": []}
        get_log_events(
            log_group_name="/aws/batch/job",
            log_stream_name="stream-1",
            start_time=1700000000000,
            end_time=1700003600000,
            limit=50,
        )
        call_kwargs = mock_logs_client.get_log_events.call_args.kwargs
        assert call_kwargs["startTime"] == 1700000000000
        assert call_kwargs["endTime"] == 1700003600000
        assert call_kwargs["limit"] == 50

    def test_omits_optional_params_when_none(self, mock_logs_client):
        mock_logs_client.get_log_events.return_value = {"events": []}
        get_log_events(
            log_group_name="/aws/batch/job",
            log_stream_name="stream-1",
        )
        call_kwargs = mock_logs_client.get_log_events.call_args.kwargs
        assert "startTime" not in call_kwargs
        assert "endTime" not in call_kwargs

    def test_returns_error_on_client_error(self, mock_logs_client):
        mock_logs_client.get_log_events.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Stream not found"}},
            "GetLogEvents",
        )
        result = get_log_events(log_group_name="/aws/batch/job", log_stream_name="bad-stream")
        assert result["success"] is False
        assert "ResourceNotFoundException" in result["error"]

    def test_returns_error_when_no_client(self):
        with patch("app.services.cloudwatch_client._get_cloudwatch_logs_client", return_value=None):
            result = get_log_events(
                log_group_name="/aws/batch/job",
                log_stream_name="stream-1",
            )
        assert result["success"] is False
        assert "boto3 not available" in result["error"]

    def test_returns_error_when_credentials_missing(self):
        with patch(
            "app.services.cloudwatch_client.require_aws_credentials",
            return_value={"success": False, "error": "Missing AWS credentials"},
        ):
            result = get_log_events(
                log_group_name="/aws/batch/job",
                log_stream_name="stream-1",
            )
        assert result["success"] is False
