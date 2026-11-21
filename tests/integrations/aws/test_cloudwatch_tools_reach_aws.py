"""CloudWatch tools build valid requests for the configured region.

Real botocore clients are used; only the network call is replaced, so request
validation and region resolution run exactly as they do against AWS.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

import botocore.client
import pytest

from integrations.cloudwatch.tools.cloudwatch_batch_metrics_tool import (
    get_cloudwatch_batch_metrics,
)
from integrations.cloudwatch.tools.cloudwatch_logs_tool import get_cloudwatch_logs


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> list[tuple[str, str, dict[str, Any]]]:
    # Only AWS_REGION is set, as the docs tell users to do.
    monkeypatch.delenv("AWS_DEFAULT_REGION", raising=False)
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    calls: list[tuple[str, str, dict[str, Any]]] = []

    def fake_api_call(self: Any, operation: str, params: dict[str, Any]) -> dict[str, Any]:
        calls.append((operation, self.meta.region_name, params))
        return {"Datapoints": [], "logStreams": []}

    monkeypatch.setattr(botocore.client.BaseClient, "_make_api_call", fake_api_call)
    return calls


def test_batch_metrics_send_a_bounded_window(sent: list[tuple[str, str, dict[str, Any]]]) -> None:
    result = get_cloudwatch_batch_metrics(job_queue="queue-a")

    assert "error" not in result
    [(operation, region, params)] = sent
    assert (operation, region) == ("GetMetricStatistics", "eu-west-1")
    window = params["EndTime"] - params["StartTime"]
    assert isinstance(params["StartTime"], datetime)
    assert window == timedelta(hours=1)


def test_logs_use_the_configured_region(sent: list[tuple[str, str, dict[str, Any]]]) -> None:
    result = get_cloudwatch_logs(log_group="/aws/lambda/checkout")

    assert result.get("message") == "No log streams found in log group"
    assert [(operation, region) for operation, region, _ in sent] == [
        ("DescribeLogStreams", "eu-west-1")
    ]
