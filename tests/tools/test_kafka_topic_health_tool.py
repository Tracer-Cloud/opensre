"""Tests for KafkaTopicHealthTool (function-based, @tool decorated).

Covers:
- BaseToolContract: name, description, input_schema, source metadata
- is_available: True / False / absent-key / falsy-verified
- extract_params: all connection fields forwarded correctly
- run happy path: all topics, specific topic, limit param
- run error path: integration returns available=False
- run not-configured: empty bootstrap_servers short-circuits before any broker contact
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from integrations.kafka.tools.kafka_topic_health_tool import (
    _map_get_kafka_topic_health,
    get_kafka_topic_health,
)
from tests.tools.conftest import BaseToolContract

# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

_KAFKA_SOURCES = {
    "kafka": {
        "connection_verified": True,
        "bootstrap_servers": "broker1:9092,broker2:9092",
        "security_protocol": "SASL_SSL",
        "sasl_mechanism": "PLAIN",
        "sasl_username": "alice",
        "sasl_password": "s3cr3t",
    }
}

_TOPIC_HEALTH_RESPONSE = {
    "source": "kafka",
    "available": True,
    "broker_count": 3,
    "topics_returned": 2,
    "cluster_topic_count": 5,
    "topics": [
        {
            "name": "payments",
            "partition_count": 3,
            "partitions": [
                {
                    "id": 0,
                    "leader": 1,
                    "replicas": [1, 2, 3],
                    "isr": [1, 2, 3],
                    "under_replicated": False,
                },
                {
                    "id": 1,
                    "leader": 2,
                    "replicas": [2, 3, 1],
                    "isr": [2, 3, 1],
                    "under_replicated": False,
                },
                {
                    "id": 2,
                    "leader": 3,
                    "replicas": [3, 1, 2],
                    "isr": [3],
                    "under_replicated": True,
                },
            ],
        },
        {
            "name": "events",
            "partition_count": 1,
            "partitions": [
                {
                    "id": 0,
                    "leader": 1,
                    "replicas": [1, 2],
                    "isr": [1, 2],
                    "under_replicated": False,
                }
            ],
        },
    ],
}


# ---------------------------------------------------------------------------
# Contract
# ---------------------------------------------------------------------------


class TestKafkaTopicHealthToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_kafka_topic_health.__opensre_registered_tool__


def test_metadata() -> None:
    rt = get_kafka_topic_health.__opensre_registered_tool__
    assert rt.name == "get_kafka_topic_health"
    assert rt.source == "kafka"
    assert "chat" in rt.surfaces


# ---------------------------------------------------------------------------
# is_available
# ---------------------------------------------------------------------------


class TestKafkaTopicHealthIsAvailable:
    def _rt(self):
        return get_kafka_topic_health.__opensre_registered_tool__

    def test_true_when_connection_verified(self) -> None:
        assert self._rt().is_available({"kafka": {"connection_verified": True}}) is True

    def test_false_when_connection_verified_is_false(self) -> None:
        assert self._rt().is_available({"kafka": {"connection_verified": False}}) is False

    def test_false_when_kafka_key_absent(self) -> None:
        assert self._rt().is_available({}) is False

    def test_false_when_kafka_is_empty_dict(self) -> None:
        assert self._rt().is_available({"kafka": {}}) is False

    def test_false_when_connection_verified_is_none(self) -> None:
        assert self._rt().is_available({"kafka": {"connection_verified": None}}) is False


# ---------------------------------------------------------------------------
# extract_params
# ---------------------------------------------------------------------------


class TestKafkaTopicHealthExtractParams:
    def _rt(self):
        return get_kafka_topic_health.__opensre_registered_tool__

    def test_extracts_all_connection_fields(self) -> None:
        params = self._rt().extract_params(_KAFKA_SOURCES)
        assert params["bootstrap_servers"] == "broker1:9092,broker2:9092"
        assert params["security_protocol"] == "SASL_SSL"
        assert params["sasl_mechanism"] == "PLAIN"
        assert params["sasl_username"] == "alice"
        assert params["sasl_password"] == "s3cr3t"

    def test_returns_empty_strings_when_kafka_absent(self) -> None:
        params = self._rt().extract_params({})
        assert params["bootstrap_servers"] == ""
        # security_protocol defaults to PLAINTEXT when absent
        assert params["security_protocol"] == "PLAINTEXT"
        assert params["sasl_mechanism"] == ""
        assert params["sasl_username"] == ""
        assert params["sasl_password"] == ""

    def test_strips_whitespace_from_bootstrap_servers(self) -> None:
        sources = {"kafka": {"connection_verified": True, "bootstrap_servers": "  broker:9092  "}}
        params = self._rt().extract_params(sources)
        assert params["bootstrap_servers"] == "broker:9092"


# ---------------------------------------------------------------------------
# run — happy paths
# ---------------------------------------------------------------------------


class TestKafkaTopicHealthRun:
    def test_happy_path_all_topics(self) -> None:
        with patch(
            "integrations.kafka.tools.kafka_topic_health_tool.get_topic_health",
            return_value=_TOPIC_HEALTH_RESPONSE,
        ):
            result = get_kafka_topic_health(bootstrap_servers="broker1:9092")

        assert result["available"] is True
        assert result["broker_count"] == 3
        assert result["topics_returned"] == 2
        assert result["source"] == "kafka"

    def test_happy_path_reports_under_replicated_partitions(self) -> None:
        with patch(
            "integrations.kafka.tools.kafka_topic_health_tool.get_topic_health",
            return_value=_TOPIC_HEALTH_RESPONSE,
        ):
            result = get_kafka_topic_health(bootstrap_servers="broker1:9092")

        under_rep = [p for t in result["topics"] for p in t["partitions"] if p["under_replicated"]]
        assert len(under_rep) == 1
        assert under_rep[0]["id"] == 2

    def test_happy_path_forwards_limit_arg(self) -> None:
        with patch(
            "integrations.kafka.tools.kafka_topic_health_tool.get_topic_health",
            return_value=_TOPIC_HEALTH_RESPONSE,
        ) as mock_fn:
            get_kafka_topic_health(bootstrap_servers="broker1:9092", limit=5)

        _, call_kwargs = mock_fn.call_args
        assert call_kwargs.get("limit") == 5

    def test_happy_path_specific_topic_forwards_topic_arg(self) -> None:
        single_topic_response = {
            "source": "kafka",
            "available": True,
            "broker_count": 2,
            "topics_returned": 1,
            "cluster_topic_count": 10,
            "topics": [
                {
                    "name": "payments",
                    "partition_count": 1,
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
        with patch(
            "integrations.kafka.tools.kafka_topic_health_tool.get_topic_health",
            return_value=single_topic_response,
        ) as mock_fn:
            result = get_kafka_topic_health(
                bootstrap_servers="broker1:9092",
                topic="payments",
            )

        assert result["available"] is True
        assert result["topics"][0]["name"] == "payments"
        # Verify the topic argument was forwarded to the integration function.
        _, call_kwargs = mock_fn.call_args
        assert call_kwargs.get("topic") == "payments"

    def test_happy_path_sasl_ssl_connection(self) -> None:
        with patch(
            "integrations.kafka.tools.kafka_topic_health_tool.get_topic_health",
            return_value=_TOPIC_HEALTH_RESPONSE,
        ) as mock_fn:
            result = get_kafka_topic_health(
                bootstrap_servers="broker1:9093",
                security_protocol="SASL_SSL",
                sasl_mechanism="PLAIN",
                sasl_username="alice",
                sasl_password="s3cr3t",
            )

        assert result["available"] is True
        # Verify SASL credentials were wired into the KafkaConfig forwarded to the integration.
        config_arg = mock_fn.call_args[0][0]
        assert config_arg.security_protocol == "SASL_SSL"
        assert config_arg.sasl_mechanism == "PLAIN"
        assert config_arg.sasl_username == "alice"
        assert config_arg.sasl_password == "s3cr3t"

    # ---------------------------------------------------------------------------
    # run — error / not-configured paths
    # ---------------------------------------------------------------------------

    def test_error_path_returns_unavailable_dict(self) -> None:
        fake_error = {
            "source": "kafka",
            "available": False,
            "error": "Connection refused by broker.",
        }
        with patch(
            "integrations.kafka.tools.kafka_topic_health_tool.get_topic_health",
            return_value=fake_error,
        ):
            result = get_kafka_topic_health(bootstrap_servers="bad-host:9092")

        assert result["available"] is False
        assert "error" in result
        assert result["source"] == "kafka"

    def test_error_path_propagates_exception_from_integration(self) -> None:
        # If the integration ever raises instead of returning an error dict,
        # the tool should let the exception propagate (no silent swallowing).
        with (
            patch(
                "integrations.kafka.tools.kafka_topic_health_tool.get_topic_health",
                side_effect=RuntimeError("broker timeout"),
            ),
            pytest.raises(RuntimeError, match="broker timeout"),
        ):
            get_kafka_topic_health(bootstrap_servers="broker1:9092")

    def test_not_configured_returns_unavailable_without_broker_contact(self) -> None:
        # Empty bootstrap_servers → KafkaConfig.is_configured is False.
        # The integration short-circuits before touching confluent_kafka.
        with patch(
            "integrations.kafka.tools.kafka_topic_health_tool.get_topic_health",
            wraps=__import__("integrations.kafka", fromlist=["get_topic_health"]).get_topic_health,
        ) as mock_fn:
            result = get_kafka_topic_health(bootstrap_servers="")

        assert result["available"] is False
        assert result["source"] == "kafka"
        # Confirm the integration was entered but never reached broker contact
        # (is_configured check returns early inside the integration).
        mock_fn.assert_called_once()


class TestMapGetKafkaTopicHealth:
    def test_records_entry_with_under_replicated_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_kafka_topic_health(evidence, _TOPIC_HEALTH_RESPONSE, {})

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "get_kafka_topic_health"
        assert entries[0]["summary"] == "2 topic(s) surveyed, 1 under-replicated partition(s)"

    def test_records_entry_without_under_replicated_suffix_when_all_healthy(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_kafka_topic_health(
            evidence,
            {
                "available": True,
                "topics": [
                    {
                        "name": "events",
                        "partition_count": 1,
                        "partitions": [{"id": 0, "under_replicated": False}],
                    }
                ],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "1 topic(s) surveyed"

    def test_records_nothing_when_no_topics(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_kafka_topic_health(
            evidence,
            {"available": True, "broker_count": 3, "topics_returned": 0, "topics": []},
            {},
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_get_kafka_topic_health(
            evidence, {"source": "kafka", "available": False, "error": "Not configured."}, {}
        )

        assert "catalog_entries" not in evidence
