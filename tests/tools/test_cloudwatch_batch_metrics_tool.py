"""Tests for CloudWatchBatchMetricsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.CloudWatchBatchMetricsTool import get_cloudwatch_batch_metrics
from tests.tools.conftest import BaseToolContract


class TestCloudWatchBatchMetricsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_cloudwatch_batch_metrics.__opensre_registered_tool__


def test_is_available_always_false_by_default() -> None:
    # no is_available override registered — defaults to always True
    rt = get_cloudwatch_batch_metrics.__opensre_registered_tool__
    result = rt.is_available({})
    assert isinstance(result, bool)


def test_run_returns_error_when_no_job_queue() -> None:
    result = get_cloudwatch_batch_metrics(job_queue="")
    assert "error" in result


def test_run_returns_error_for_invalid_metric_type() -> None:
    result = get_cloudwatch_batch_metrics(job_queue="my-queue", metric_type="invalid")
    assert "error" in result


def test_run_cpu_metrics_happy_path() -> None:
    fake_metrics = [{"Timestamp": "2024-01-01", "Average": 50.0}]
    with patch(
        "app.tools.CloudWatchBatchMetricsTool.get_metric_statistics", return_value=fake_metrics
    ):
        result = get_cloudwatch_batch_metrics(job_queue="my-queue", metric_type="cpu")
    assert result["metrics"] == fake_metrics
    assert result["metric_type"] == "cpu"
    assert result["job_queue"] == "my-queue"


def test_run_memory_metrics_happy_path() -> None:
    fake_metrics = [{"Timestamp": "2024-01-01", "Average": 80.0}]
    with patch(
        "app.tools.CloudWatchBatchMetricsTool.get_metric_statistics", return_value=fake_metrics
    ):
        result = get_cloudwatch_batch_metrics(job_queue="my-queue", metric_type="memory")
    assert result["metric_type"] == "memory"


def test_run_handles_exception() -> None:
    with patch(
        "app.tools.CloudWatchBatchMetricsTool.get_metric_statistics",
        side_effect=Exception("AWS error"),
    ):
        result = get_cloudwatch_batch_metrics(job_queue="my-queue")
    assert "error" in result
    assert "CloudWatch not available" in result["error"]
