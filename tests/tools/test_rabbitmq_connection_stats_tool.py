"""Tests for RabbitMQConnectionStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.RabbitMQConnectionStatsTool import get_rabbitmq_connection_stats
from tests.tools.conftest import BaseToolContract


class TestRabbitMQConnectionStatsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_rabbitmq_connection_stats.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_rabbitmq_connection_stats.__opensre_registered_tool__
    assert rt.name == "get_rabbitmq_connection_stats"
    assert rt.source == "rabbitmq"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "rabbitmq",
        "available": True,
        "total_connections": 1,
        "returned": 1,
        "connections": [
            {"name": "app-1", "user": "admin", "vhost": "/", "recv_rate_bps": 1024.0}
        ],
    }
    with patch(
        "app.tools.RabbitMQConnectionStatsTool.get_connection_stats",
        return_value=fake_result,
    ):
        result = get_rabbitmq_connection_stats(host="rmq", username="admin")
    assert result["available"] is True
    assert result["total_connections"] == 1
