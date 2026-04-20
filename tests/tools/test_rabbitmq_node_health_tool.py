"""Tests for RabbitMQNodeHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.RabbitMQNodeHealthTool import get_rabbitmq_node_health
from tests.tools.conftest import BaseToolContract


class TestRabbitMQNodeHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_rabbitmq_node_health.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_rabbitmq_node_health.__opensre_registered_tool__
    assert rt.name == "get_rabbitmq_node_health"
    assert rt.source == "rabbitmq"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "rabbitmq",
        "available": True,
        "node_count": 2,
        "any_partitioned": False,
        "nodes": [
            {"name": "rmq@node1", "running": True, "partitions": []},
            {"name": "rmq@node2", "running": True, "partitions": []},
        ],
    }
    with patch(
        "app.tools.RabbitMQNodeHealthTool.get_node_health",
        return_value=fake_result,
    ):
        result = get_rabbitmq_node_health(host="rmq", username="admin")
    assert result["available"] is True
    assert result["node_count"] == 2
    assert result["any_partitioned"] is False
