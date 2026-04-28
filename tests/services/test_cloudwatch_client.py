"""Direct unit tests for app/services/cloudwatch_client.py.

Tests the service functions in isolation — no tool-registration checks here.
Mocks make_boto3_client() and require_aws_credentials() to avoid real AWS calls.

See: https://github.com/Tracer-Cloud/opensre/issues/885
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.services.cloudwatch_client import (
    filter_log_events,
    get_log_events,
    get_metric_statistics,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_boto3(return_value: dict):
    """Return a mock boto3 client whose method calls return *return_value*."""
    client = MagicMock()
    client.get_metric_statistics.return_value = return_value
    client.filter_log_events.return_value = return_value
    client.get_log_events.return_value = return_value
    return client


# ─────────────────────────────────────────────────────────────────────────────
# get_metric_statistics
# ─────────────────────────────────────────────────────────────────────────────

class TestGetMetricStatistics:
    def test_returns_data_on_success(self):
        mock_response = {"Datapoints": [{"Average": 42.0, "Unit": "Percent"}], "Label": "CPUUtilization"}
        boto_client = _mock_boto3(mock_response)
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None),
        ):
            result = get_metric_statistics(
                namespace="AWS/EC2",
                metric_name="CPUUtilization",
                start_time="2024-01-01T00:00:00Z",
                end_time="2024-01-01T01:00:00Z",
            )
        assert result["success"] is True
        assert "data" in result

    def test_returns_error_when_boto3_unavailable(self):
        with patch("app.services.cloudwatch_client.make_boto3_client", return_value=None):
            result = get_metric_statistics(
                namespace="AWS/EC2",
                metric_name="CPUUtilization",
            )
        assert result["success"] is False
        assert "boto3" in result["error"].lower()

    def test_returns_error_when_credentials_missing(self):
        boto_client = _mock_boto3({})
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch(
                "app.services.cloudwatch_client.require_aws_credentials",
                return_value={"success": False, "error": "no credentials"},
            ),
        ):
            result = get_metric_statistics(
                namespace="AWS/EC2",
                metric_name="CPUUtilization",
            )
        assert result["success"] is False

    def test_uses_default_statistics_when_not_provided(self):
        boto_client = _mock_boto3({"Datapoints": []})
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None),
        ):
            get_metric_statistics(namespace="AWS/EC2", metric_name="CPUUtilization")

        call_kwargs = boto_client.get_metric_statistics.call_args[1]
        assert "Average" in call_kwargs["Statistics"]
        assert "Maximum" in call_kwargs["Statistics"]

    def test_handles_client_error(self):
        from botocore.exceptions import ClientError

        boto_client = MagicMock()
        boto_client.get_metric_statistics.side_effect = ClientError(
            {"Error": {"Code": "AccessDenied", "Message": "Access denied"}}, "GetMetricStatistics"
        )
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None),
        ):
            result = get_metric_statistics(namespace="AWS/EC2", metric_name="CPUUtilization")
        assert result["success"] is False
        assert "error" in result


# ─────────────────────────────────────────────────────────────────────────────
# filter_log_events
# ─────────────────────────────────────────────────────────────────────────────

class TestFilterLogEvents:
    def test_returns_events_on_success(self):
        mock_response = {"events": [{"message": "ERROR: timeout", "timestamp": 1700000000000}]}
        boto_client = _mock_boto3(mock_response)
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None),
        ):
            result = filter_log_events(
                log_group_name="/aws/batch/job",
                filter_pattern="ERROR",
            )
        assert result["success"] is True
        assert isinstance(result["data"], list)

    def test_returns_error_when_boto3_unavailable(self):
        with patch("app.services.cloudwatch_client.make_boto3_client", return_value=None):
            result = filter_log_events(log_group_name="/aws/batch/job")
        assert result["success"] is False

    def test_returns_error_when_credentials_missing(self):
        boto_client = _mock_boto3({})
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch(
                "app.services.cloudwatch_client.require_aws_credentials",
                return_value={"success": False, "error": "no credentials"},
            ),
        ):
            result = filter_log_events(log_group_name="/aws/batch/job")
        assert result["success"] is False

    def test_omits_optional_params_when_not_provided(self):
        boto_client = _mock_boto3({"events": []})
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None),
        ):
            filter_log_events(log_group_name="/aws/batch/job")

        call_kwargs = boto_client.filter_log_events.call_args[1]
        assert "filterPattern" not in call_kwargs
        assert "startTime" not in call_kwargs

    def test_includes_filter_pattern_when_provided(self):
        boto_client = _mock_boto3({"events": []})
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None),
        ):
            filter_log_events(log_group_name="/aws/batch/job", filter_pattern="CRITICAL")

        call_kwargs = boto_client.filter_log_events.call_args[1]
        assert call_kwargs["filterPattern"] == "CRITICAL"


# ─────────────────────────────────────────────────────────────────────────────
# get_log_events
# ─────────────────────────────────────────────────────────────────────────────

class TestGetLogEvents:
    def test_returns_events_on_success(self):
        mock_response = {"events": [{"message": "Job started", "timestamp": 1700000000000}]}
        boto_client = _mock_boto3(mock_response)
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None),
        ):
            result = get_log_events(
                log_group_name="/aws/batch/job",
                log_stream_name="job-123/container/abc",
            )
        assert result["success"] is True
        assert isinstance(result["data"], list)

    def test_returns_error_when_boto3_unavailable(self):
        with patch("app.services.cloudwatch_client.make_boto3_client", return_value=None):
            result = get_log_events(
                log_group_name="/aws/batch/job",
                log_stream_name="stream",
            )
        assert result["success"] is False

    def test_returns_error_when_credentials_missing(self):
        boto_client = _mock_boto3({})
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch(
                "app.services.cloudwatch_client.require_aws_credentials",
                return_value={"success": False, "error": "no credentials"},
            ),
        ):
            result = get_log_events(
                log_group_name="/aws/batch/job",
                log_stream_name="stream",
            )
        assert result["success"] is False

    def test_handles_client_error(self):
        from botocore.exceptions import ClientError

        boto_client = MagicMock()
        boto_client.get_log_events.side_effect = ClientError(
            {"Error": {"Code": "ResourceNotFoundException", "Message": "Log group not found"}},
            "GetLogEvents",
        )
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None),
        ):
            result = get_log_events(
                log_group_name="/aws/batch/missing",
                log_stream_name="stream",
            )
        assert result["success"] is False
        assert "error" in result

    def test_passes_time_range_when_provided(self):
        boto_client = _mock_boto3({"events": []})
        with (
            patch("app.services.cloudwatch_client.make_boto3_client", return_value=boto_client),
            patch("app.services.cloudwatch_client.require_aws_credentials", return_value=None),
        ):
            get_log_events(
                log_group_name="/aws/batch/job",
                log_stream_name="stream",
                start_time=1700000000000,
                end_time=1700003600000,
            )

        call_kwargs = boto_client.get_log_events.call_args[1]
        assert call_kwargs["startTime"] == 1700000000000
        assert call_kwargs["endTime"] == 1700003600000
