"""Tests for RabbitMQQueueBacklogTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.RabbitMQQueueBacklogTool import get_rabbitmq_queue_backlog
from tests.tools.conftest import BaseToolContract


class TestRabbitMQQueueBacklogToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_rabbitmq_queue_backlog.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_rabbitmq_queue_backlog.__opensre_registered_tool__
    assert rt.name == "get_rabbitmq_queue_backlog"
    assert rt.source == "rabbitmq"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "rabbitmq",
        "available": True,
        "total_queues": 1,
        "returned": 1,
        "queues": [{"name": "orders", "messages_ready": 100, "messages_unacknowledged": 5}],
    }
    with patch(
        "app.tools.RabbitMQQueueBacklogTool.get_queue_backlog",
        return_value=fake_result,
    ):
        result = get_rabbitmq_queue_backlog(host="rmq", username="admin")
    assert result["available"] is True
    assert result["total_queues"] == 1


def test_run_error_path() -> None:
    with patch(
        "app.tools.RabbitMQQueueBacklogTool.get_queue_backlog",
        return_value={
            "source": "rabbitmq",
            "available": False,
            "error": "connection refused",
        },
    ):
        result = get_rabbitmq_queue_backlog(host="invalid", username="admin")
    assert result["available"] is False
    assert "error" in result
