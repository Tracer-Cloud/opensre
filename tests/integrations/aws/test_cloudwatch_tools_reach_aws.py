"""CloudWatch tools build valid requests for the configured region.

Real botocore clients are used and only the HTTP send is replaced, so region
resolution, parameter validation and serialization all run as against AWS.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import botocore.endpoint
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

    def fake_send(
        self: Any, operation_model: Any, request_dict: dict[str, Any]
    ) -> tuple[Any, dict[str, Any]]:
        # Reached only after botocore validated and serialized the parameters.
        calls.append((operation_model.name, request_dict["url"], request_dict["body"]))
        return SimpleNamespace(status_code=200, headers={}), {"Datapoints": [], "logStreams": []}

    monkeypatch.setattr(botocore.endpoint.Endpoint, "make_request", fake_send)
    return calls


def test_batch_metrics_send_a_bounded_window(sent: list[tuple[str, str, dict[str, Any]]]) -> None:
    result = get_cloudwatch_batch_metrics(job_queue="queue-a")

    assert "error" not in result
    [(operation, url, body)] = sent
    assert operation == "GetMetricStatistics"
    assert "monitoring.eu-west-1.amazonaws.com" in url
    # Both bounds were validated and serialized as epoch timestamps.
    sent_params = json.loads(body)
    assert sent_params["EndTime"] - sent_params["StartTime"] == pytest.approx(3600)
    assert sent_params["Dimensions"] == [{"Name": "JobQueue", "Value": "queue-a"}]


def test_logs_use_the_configured_region(sent: list[tuple[str, str, dict[str, Any]]]) -> None:
    result = get_cloudwatch_logs(log_group="/aws/lambda/checkout")

    assert result.get("message") == "No log streams found in log group"
    [(operation, url, _body)] = sent
    assert operation == "DescribeLogStreams"
    assert "logs.eu-west-1.amazonaws.com" in url
