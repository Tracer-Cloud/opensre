"""Tests for KafkaTopicHealthTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.KafkaTopicHealthTool import get_kafka_topic_health
from tests.tools.conftest import BaseToolContract


class TestKafkaTopicHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_kafka_topic_health.__opensre_registered_tool__


def test_is_available_requires_bootstrap_servers() -> None:
    rt = get_kafka_topic_health.__opensre_registered_tool__
    assert rt.is_available({"kafka": {"bootstrap_servers": "localhost:9092"}}) is True
    assert rt.is_available({"kafka": {"bootstrap_servers": ""}}) is False
    assert rt.is_available({"kafka": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_kafka_topic_health.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "kafka": {
                "bootstrap_servers": "broker1:9092,broker2:9092",
                "topic": "orders",
                "security_protocol": "SASL_SSL",
                "sasl_mechanism": "PLAIN",
                "sasl_username": "alice",
                "sasl_password": "secret",
            }
        }
    )
    assert params["bootstrap_servers"] == "broker1:9092,broker2:9092"
    assert params["topic"] == "orders"
    assert params["security_protocol"] == "SASL_SSL"
    assert params["sasl_mechanism"] == "PLAIN"
    assert params["sasl_username"] == "alice"
    assert params["sasl_password"] == "secret"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "kafka",
        "available": True,
        "broker_count": 3,
        "topics_returned": 1,
        "cluster_topic_count": 12,
        "topics": [
            {
                "name": "orders",
                "partition_count": 2,
                "partitions": [
                    {
                        "id": 0,
                        "leader": 1,
                        "replicas": [1, 2],
                        "isr": [1, 2],
                        "under_replicated": False,
                    }
                ],
            }
        ],
    }
    with patch("app.tools.KafkaTopicHealthTool.get_topic_health", return_value=fake_result) as mock_get_topic_health:
        result = get_kafka_topic_health(
            bootstrap_servers="localhost:9092",
            topic="orders",
            security_protocol="SASL_SSL",
            sasl_mechanism="PLAIN",
            sasl_username="alice",
            sasl_password="secret",
            limit=5,
        )

    assert result["available"] is True
    assert result["topics_returned"] == 1
    config = mock_get_topic_health.call_args.args[0]
    assert config.bootstrap_servers == "localhost:9092"
    assert config.security_protocol == "SASL_SSL"
    assert config.sasl_mechanism == "PLAIN"
    assert mock_get_topic_health.call_args.kwargs == {"topic": "orders", "limit": 5}


def test_run_error_path_returns_graceful_error() -> None:
    with patch(
        "app.tools.KafkaTopicHealthTool.get_topic_health",
        side_effect=RuntimeError("No brokers available"),
    ):
        result = get_kafka_topic_health(
            bootstrap_servers="localhost:9092",
            topic="orders",
        )

    assert result["available"] is False
    assert "No brokers available" in result["error"]
