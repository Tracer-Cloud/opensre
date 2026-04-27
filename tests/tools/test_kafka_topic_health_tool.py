"""Unit tests for KafkaTopicHealthTool (get_kafka_topic_health)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.KafkaTopicHealthTool import get_kafka_topic_health
from tests.tools.conftest import BaseToolContract


class TestKafkaTopicHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_kafka_topic_health.__opensre_registered_tool__


# ── is_available ────────────────────────────────────────────────────
# kafka_is_available checks sources["kafka"]["connection_verified"], not
# bootstrap_servers directly. Positive test must include connection_verified.

def test_is_available_true_when_connection_verified() -> None:
    rt = get_kafka_topic_health.__opensre_registered_tool__
    sources = {"kafka": {"connection_verified": True, "bootstrap_servers": "localhost:9092"}}
    assert rt.is_available(sources) is True


def test_is_available_false_missing_connection_verified() -> None:
    rt = get_kafka_topic_health.__opensre_registered_tool__
    # bootstrap_servers present but connection_verified absent → False
    assert rt.is_available({"kafka": {"bootstrap_servers": "localhost:9092"}}) is False


def test_is_available_false_missing_kafka_key() -> None:
    rt = get_kafka_topic_health.__opensre_registered_tool__
    assert rt.is_available({}) is False


# ── extract_params ──────────────────────────────────────────────────
# kafka_extract_params maps fields from sources["kafka"]; the tool then
# receives bootstrap_servers, security_protocol, etc. as keyword args.

def test_extract_params_maps_bootstrap_servers() -> None:
    rt = get_kafka_topic_health.__opensre_registered_tool__
    sources = {
        "kafka": {
            "connection_verified": True,
            "bootstrap_servers": "broker:9092",
            "topic": "my-topic",
            "security_protocol": "PLAINTEXT",
        }
    }
    params = rt.extract_params(sources)
    assert params["bootstrap_servers"] == "broker:9092"


# ── run ─────────────────────────────────────────────────────────────

def test_run_returns_topic_health_on_success() -> None:
    mock_result = {
        "available": True,
        "topics": [
            {
                "topic": "my-topic",
                "partitions": [{"id": 0, "leader": 1, "replicas": [1], "isr": [1]}],
            }
        ],
    }
    with patch(
        "app.tools.KafkaTopicHealthTool.get_topic_health",
        return_value=mock_result,
    ):
        result = get_kafka_topic_health(
            bootstrap_servers="localhost:9092",
            topic="my-topic",
        )
    assert result["available"] is True
    assert "topics" in result


def test_run_returns_error_on_exception() -> None:
    with patch(
        "app.tools.KafkaTopicHealthTool.get_topic_health",
        side_effect=Exception("NoBrokersAvailable"),
    ):
        result = get_kafka_topic_health(bootstrap_servers="localhost:9092")
    assert result["available"] is False
    assert "error" in result
