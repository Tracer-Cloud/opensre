"""Tests for RabbitMQConsumerHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.RabbitMQConsumerHealthTool import get_rabbitmq_consumer_health
from tests.tools.conftest import BaseToolContract


class TestRabbitMQConsumerHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_rabbitmq_consumer_health.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_rabbitmq_consumer_health.__opensre_registered_tool__
    assert rt.name == "get_rabbitmq_consumer_health"
    assert rt.source == "rabbitmq"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "rabbitmq",
        "available": True,
        "total_consumers": 2,
        "returned": 2,
        "consumers": [
            {"queue": "orders", "consumer_tag": "amq.ctag-1", "prefetch_count": 10},
            {"queue": "billing", "consumer_tag": "amq.ctag-2", "prefetch_count": 5},
        ],
    }
    with patch(
        "app.tools.RabbitMQConsumerHealthTool.get_consumer_health",
        return_value=fake_result,
    ):
        result = get_rabbitmq_consumer_health(host="rmq", username="admin")
    assert result["available"] is True
    assert result["total_consumers"] == 2
