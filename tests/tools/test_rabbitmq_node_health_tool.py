"""Tests for RabbitMQNodeHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

from integrations.rabbitmq.tools.rabbitmq_node_health_tool import (
    _map_get_rabbitmq_node_health,
    get_rabbitmq_node_health,
)
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
        "integrations.rabbitmq.tools.rabbitmq_node_health_tool.get_node_health",
        return_value=fake_result,
    ):
        result = get_rabbitmq_node_health(host="rmq", username="admin")
    assert result["available"] is True
    assert result["node_count"] == 2
    assert result["any_partitioned"] is False


class TestMapGetRabbitmqNodeHealth:
    def test_records_entry_with_partition_and_alarm(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_node_health(
            evidence,
            {
                "available": True,
                "node_count": 2,
                "any_partitioned": True,
                "nodes": [
                    {"name": "rmq@node1", "mem_alarm": True, "disk_free_alarm": False},
                    {"name": "rmq@node2", "mem_alarm": False, "disk_free_alarm": False},
                ],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_rabbitmq_node_health"
        assert entries[0]["summary"] == "2 node(s), cluster partitioned, alarm on rmq@node1"

    def test_records_entry_without_partition_or_alarm_clauses(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_node_health(
            evidence,
            {
                "available": True,
                "node_count": 1,
                "any_partitioned": False,
                "nodes": [{"name": "rmq@node1", "mem_alarm": False, "disk_free_alarm": False}],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 node(s)"

    def test_records_nothing_when_no_nodes(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_node_health(
            evidence, {"available": True, "node_count": 0, "nodes": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_rabbitmq_node_health(
            evidence, {"available": False, "error": "connection refused"}, {}
        )

        assert "catalog_entries" not in evidence
