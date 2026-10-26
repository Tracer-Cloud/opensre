"""Tests for RabbitMQConsumerHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.rabbitmq.tools.rabbitmq_consumer_health_tool import (
    _map_get_rabbitmq_consumer_health,
    get_rabbitmq_consumer_health,
)
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
        "integrations.rabbitmq.tools.rabbitmq_consumer_health_tool.get_consumer_health",
        return_value=fake_result,
    ):
        result = get_rabbitmq_consumer_health(host="rmq", username="admin")
    assert result["available"] is True
    assert result["total_consumers"] == 2


class TestMapGetRabbitmqConsumerHealth:
    def test_records_entry_with_inactive_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_consumer_health(
            evidence,
            {
                "available": True,
                "total_consumers": 2,
                "consumers": [
                    {"queue": "orders", "active": True},
                    {"queue": "billing", "active": False},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_rabbitmq_consumer_health"
        assert entries[0]["summary"] == "2 consumer(s), 1 inactive"

    def test_qualifies_inactive_count_when_list_is_truncated(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_consumer_health(
            evidence,
            {
                "available": True,
                "total_consumers": 100,
                "returned": 2,
                "consumers": [
                    {"queue": "orders", "active": True},
                    {"queue": "billing", "active": False},
                ],
            },
            {},
        )

        assert (
            evidence["catalog_entries"][0]["summary"]
            == "100 consumer(s), 1 of the 2 shown are inactive"
        )

    def test_records_entry_without_inactive_clause_when_all_active(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_consumer_health(
            evidence,
            {
                "available": True,
                "total_consumers": 1,
                "consumers": [{"queue": "orders", "active": True}],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 consumer(s)"

    def test_records_nothing_when_no_consumers(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_consumer_health(
            evidence, {"available": True, "total_consumers": 0, "consumers": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_consumer_health(
            evidence, {"available": False, "error": "connection refused"}, {}
        )

        assert "catalog_entries" not in evidence
