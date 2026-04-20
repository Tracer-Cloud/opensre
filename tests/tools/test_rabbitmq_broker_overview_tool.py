"""Tests for RabbitMQBrokerOverviewTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.RabbitMQBrokerOverviewTool import get_rabbitmq_broker_overview
from tests.tools.conftest import BaseToolContract


class TestRabbitMQBrokerOverviewToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_rabbitmq_broker_overview.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_rabbitmq_broker_overview.__opensre_registered_tool__
    assert rt.name == "get_rabbitmq_broker_overview"
    assert rt.source == "rabbitmq"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "rabbitmq",
        "available": True,
        "cluster_name": "rmq@node1",
        "rabbitmq_version": "3.13.0",
        "messages_total": 42,
        "alarms": {"ok": True, "detail": "ok"},
    }
    with patch(
        "app.tools.RabbitMQBrokerOverviewTool.get_broker_overview",
        return_value=fake_result,
    ):
        result = get_rabbitmq_broker_overview(host="rmq", username="admin")
    assert result["available"] is True
    assert result["rabbitmq_version"] == "3.13.0"
    assert result["alarms"]["ok"] is True
