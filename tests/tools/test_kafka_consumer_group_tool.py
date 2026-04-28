"""Tests for KafkaConsumerGroupTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.KafkaConsumerGroupTool import get_kafka_consumer_group_lag
from tests.tools.conftest import BaseToolContract


class TestKafkaConsumerGroupToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_kafka_consumer_group_lag.__opensre_registered_tool__


def test_is_available_requires_bootstrap_servers() -> None:
    rt = get_kafka_consumer_group_lag.__opensre_registered_tool__
    assert rt.is_available({"kafka": {"bootstrap_servers": "localhost:9092"}}) is True
    assert rt.is_available({"kafka": {"bootstrap_servers": ""}}) is False
    assert rt.is_available({"kafka": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_kafka_consumer_group_lag.__opensre_registered_tool__
    params = rt.extract_params(
        {
            "kafka": {
                "bootstrap_servers": "broker1:9092,broker2:9092",
                "group_id": "payments-consumer",
                "security_protocol": "SASL_SSL",
                "sasl_mechanism": "SCRAM-SHA-512",
                "sasl_username": "alice",
                "sasl_password": "secret",
            }
        }
    )
    assert params["bootstrap_servers"] == "broker1:9092,broker2:9092"
    assert params["group_id"] == "payments-consumer"
    assert params["security_protocol"] == "SASL_SSL"
    assert params["sasl_mechanism"] == "SCRAM-SHA-512"
    assert params["sasl_username"] == "alice"
    assert params["sasl_password"] == "secret"


def test_run_happy_path() -> None:
    fake_result = {
        "source": "kafka",
        "available": True,
        "group_id": "payments-consumer",
        "partition_count": 2,
        "total_lag": 11,
        "partitions": [
            {
                "topic": "payments",
                "partition": 0,
                "current_offset": 120,
                "high_watermark": 125,
                "lag": 5,
            },
            {
                "topic": "payments",
                "partition": 1,
                "current_offset": 98,
                "high_watermark": 104,
                "lag": 6,
            },
        ],
    }
    with patch(
        "app.tools.KafkaConsumerGroupTool.get_consumer_group_lag",
        return_value=fake_result,
    ) as mock_get_consumer_group_lag:
        result = get_kafka_consumer_group_lag(
            bootstrap_servers="localhost:9092",
            group_id="payments-consumer",
            security_protocol="SASL_SSL",
            sasl_mechanism="SCRAM-SHA-512",
            sasl_username="alice",
            sasl_password="secret",
        )

    assert result["available"] is True
    assert result["group_id"] == "payments-consumer"
    config = mock_get_consumer_group_lag.call_args.args[0]
    assert config.bootstrap_servers == "localhost:9092"
    assert config.security_protocol == "SASL_SSL"
    assert config.sasl_mechanism == "SCRAM-SHA-512"
    assert mock_get_consumer_group_lag.call_args.kwargs == {
        "group_id": "payments-consumer"
    }


def test_run_error_path_returns_graceful_error() -> None:
    with patch(
        "app.tools.KafkaConsumerGroupTool.get_consumer_group_lag",
        side_effect=RuntimeError("No brokers available"),
    ):
        result = get_kafka_consumer_group_lag(
            bootstrap_servers="localhost:9092",
            group_id="payments-consumer",
        )

    assert result["available"] is False
    assert "No brokers available" in result["error"]
