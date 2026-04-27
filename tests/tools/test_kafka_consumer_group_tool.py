"""Unit tests for KafkaConsumerGroupTool (get_kafka_consumer_group_lag)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.KafkaConsumerGroupTool import get_kafka_consumer_group_lag
from tests.tools.conftest import BaseToolContract


class TestKafkaConsumerGroupToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_kafka_consumer_group_lag.__opensre_registered_tool__


# ── is_available ────────────────────────────────────────────────────
# kafka_is_available checks sources["kafka"]["connection_verified"].

def test_is_available_true_when_connection_verified() -> None:
    rt = get_kafka_consumer_group_lag.__opensre_registered_tool__
    sources = {"kafka": {"connection_verified": True, "bootstrap_servers": "localhost:9092"}}
    assert rt.is_available(sources) is True


def test_is_available_false_missing_connection_verified() -> None:
    rt = get_kafka_consumer_group_lag.__opensre_registered_tool__
    assert rt.is_available({"kafka": {"bootstrap_servers": "broker:9092"}}) is False


def test_is_available_false_missing_kafka_key() -> None:
    rt = get_kafka_consumer_group_lag.__opensre_registered_tool__
    assert rt.is_available({}) is False


# ── extract_params ──────────────────────────────────────────────────

def test_extract_params_maps_bootstrap_servers() -> None:
    rt = get_kafka_consumer_group_lag.__opensre_registered_tool__
    sources = {
        "kafka": {
            "connection_verified": True,
            "bootstrap_servers": "broker:9092",
            "group_id": "my-consumer-group",
        }
    }
    params = rt.extract_params(sources)
    assert params["bootstrap_servers"] == "broker:9092"


# ── run ─────────────────────────────────────────────────────────────

def test_run_returns_lag_info_on_success() -> None:
    mock_result = {
        "available": True,
        "group_id": "my-consumer-group",
        "lag": [
            {"topic": "events", "partition": 0, "lag": 142},
        ],
    }
    with patch(
        "app.tools.KafkaConsumerGroupTool.get_consumer_group_lag",
        return_value=mock_result,
    ):
        result = get_kafka_consumer_group_lag(
            bootstrap_servers="broker:9092",
            group_id="my-consumer-group",
        )
    assert result["available"] is True
    assert "lag" in result


def test_run_returns_error_on_exception() -> None:
    with patch(
        "app.tools.KafkaConsumerGroupTool.get_consumer_group_lag",
        side_effect=Exception("NoBrokersAvailable"),
    ):
        result = get_kafka_consumer_group_lag(
            bootstrap_servers="broker:9092",
            group_id="my-consumer-group",
        )
    assert result["available"] is False
    assert "error" in result
