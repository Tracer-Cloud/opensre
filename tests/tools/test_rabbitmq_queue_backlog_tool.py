"""Tests for RabbitMQQueueBacklogTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.rabbitmq.tools.rabbitmq_queue_backlog_tool import (
    _map_get_rabbitmq_queue_backlog,
    get_rabbitmq_queue_backlog,
)
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
        "integrations.rabbitmq.tools.rabbitmq_queue_backlog_tool.get_queue_backlog",
        return_value=fake_result,
    ):
        result = get_rabbitmq_queue_backlog(host="rmq", username="admin")
    assert result["available"] is True
    assert result["total_queues"] == 1


def test_run_error_path() -> None:
    with patch(
        "integrations.rabbitmq.tools.rabbitmq_queue_backlog_tool.get_queue_backlog",
        return_value={
            "source": "rabbitmq",
            "available": False,
            "error": "connection refused",
        },
    ):
        result = get_rabbitmq_queue_backlog(host="invalid", username="admin")
    assert result["available"] is False
    assert "error" in result


class TestMapGetRabbitmqQueueBacklog:
    def test_records_entry_with_zero_consumer_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_queue_backlog(
            evidence,
            {
                "available": True,
                "total_queues": 2,
                "queues": [
                    {
                        "name": "orders",
                        "messages_ready": 100,
                        "messages_unacknowledged": 5,
                        "consumers": 0,
                    },
                    {
                        "name": "emails",
                        "messages_ready": 3,
                        "messages_unacknowledged": 0,
                        "consumers": 1,
                    },
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_rabbitmq_queue_backlog"
        assert (
            entries[0]["summary"]
            == "2 queue(s), top backlog 105 on 'orders', 1 with zero consumers"
        )

    def test_qualifies_zero_consumer_count_when_list_is_truncated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_queue_backlog(
            evidence,
            {
                "available": True,
                "total_queues": 100,
                "returned": 2,
                "queues": [
                    {
                        "name": "orders",
                        "messages_ready": 100,
                        "messages_unacknowledged": 5,
                        "consumers": 0,
                    },
                    {
                        "name": "emails",
                        "messages_ready": 3,
                        "messages_unacknowledged": 0,
                        "consumers": 1,
                    },
                ],
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "100 queue(s), top backlog 105 on 'orders', 1 of the 2 shown have zero consumers"
        )

    def test_records_entry_without_zero_consumer_clause(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_queue_backlog(
            evidence,
            {
                "available": True,
                "total_queues": 1,
                "queues": [
                    {
                        "name": "orders",
                        "messages_ready": 10,
                        "messages_unacknowledged": 0,
                        "consumers": 2,
                    }
                ],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 queue(s), top backlog 10 on 'orders'"

    def test_records_nothing_when_no_queues(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_queue_backlog(
            evidence, {"available": True, "total_queues": 0, "queues": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_queue_backlog(
            evidence, {"available": False, "error": "connection refused"}, {}
        )

        assert "catalog_entries" not in evidence
