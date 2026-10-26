"""Tests for RabbitMQConnectionStatsTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.rabbitmq.tools.rabbitmq_connection_stats_tool import (
    _map_get_rabbitmq_connection_stats,
    get_rabbitmq_connection_stats,
)
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
        "broker_total_connections": 3,
        "vhost_connections": 1,
        "returned": 1,
        "connections": [
            {"name": "app-1", "user": "admin", "vhost": "/", "recv_rate_bytes_per_sec": 1024.0}
        ],
    }
    with patch(
        "integrations.rabbitmq.tools.rabbitmq_connection_stats_tool.get_connection_stats",
        return_value=fake_result,
    ):
        result = get_rabbitmq_connection_stats(host="rmq", username="admin")
    assert result["available"] is True
    assert result["vhost_connections"] == 1


class TestMapGetRabbitmqConnectionStats:
    def test_records_entry_with_broker_total(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_connection_stats(
            evidence,
            {
                "available": True,
                "broker_total_connections": 3,
                "vhost_connections": 1,
                "connections": [{"name": "app-1"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_rabbitmq_connection_stats"
        assert entries[0]["summary"] == "1 connection(s) in vhost (of 3 broker-wide)"

    def test_records_nothing_when_no_connections(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_connection_stats(
            evidence,
            {
                "available": True,
                "broker_total_connections": 0,
                "vhost_connections": 0,
                "connections": [],
            },
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_connection_stats(
            evidence, {"available": False, "error": "connection refused"}, {}
        )

        assert "catalog_entries" not in evidence
