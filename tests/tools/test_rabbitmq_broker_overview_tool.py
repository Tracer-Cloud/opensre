"""Tests for RabbitMQBrokerOverviewTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.rabbitmq.tools.rabbitmq_broker_overview_tool import (
    _map_get_rabbitmq_broker_overview,
    get_rabbitmq_broker_overview,
)
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
        "integrations.rabbitmq.tools.rabbitmq_broker_overview_tool.get_broker_overview",
        return_value=fake_result,
    ):
        result = get_rabbitmq_broker_overview(host="rmq", username="admin")
    assert result["available"] is True
    assert result["rabbitmq_version"] == "3.13.0"
    assert result["alarms"]["ok"] is True


class TestMapGetRabbitmqBrokerOverview:
    def test_records_entry_with_alarm(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_broker_overview(
            evidence,
            {
                "available": True,
                "queues": 12,
                "messages_ready": 500,
                "messages_unacknowledged": 3,
                "alarms": {"ok": False, "detail": "memory alarm"},
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_rabbitmq_broker_overview"
        assert entries[0]["summary"] == "12 queue(s), 500 ready, 3 unacked, ALARM: memory alarm"

    def test_records_entry_without_alarm_clause_when_ok(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_broker_overview(
            evidence,
            {
                "available": True,
                "queues": 2,
                "messages_ready": 0,
                "messages_unacknowledged": 0,
                "alarms": {"ok": True, "detail": "ok"},
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "2 queue(s), 0 ready, 0 unacked"

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_broker_overview(evidence, {"available": False, "error": "timeout"}, {})

        assert "catalog_entries" not in evidence
