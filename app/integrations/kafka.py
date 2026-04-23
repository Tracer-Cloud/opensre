"""Shared Kafka integration helpers.

Provides configuration, connectivity validation, and read-only diagnostic
queries for Kafka clusters. All operations are read-only: topic metadata,
consumer group lag, and broker health. No produce or consume operations.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from pydantic import Field, field_validator

from app.strict_config import StrictConfigModel

DEFAULT_KAFKA_SECURITY_PROTOCOL = "PLAINTEXT"
DEFAULT_KAFKA_TIMEOUT_SECONDS = 10.0
DEFAULT_KAFKA_MAX_RESULTS = 50


class KafkaConfig(StrictConfigModel):
    """Normalized Kafka connection settings."""

    bootstrap_servers: str = ""
    security_protocol: str = DEFAULT_KAFKA_SECURITY_PROTOCOL
    sasl_mechanism: str = ""
    sasl_username: str = ""
    sasl_password: str = ""
    timeout_seconds: float = Field(default=DEFAULT_KAFKA_TIMEOUT_SECONDS, gt=0)
    max_results: int = Field(default=DEFAULT_KAFKA_MAX_RESULTS, gt=0, le=200)
    integration_id: str = ""

    @field_validator("bootstrap_servers", mode="before")
    @classmethod
    def _normalize_bootstrap_servers(cls, value: Any) -> str:
        return str(value or "").strip()

    @field_validator("security_protocol", mode="before")
    @classmethod
    def _normalize_security_protocol(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_KAFKA_SECURITY_PROTOCOL).strip().upper()
        return normalized or DEFAULT_KAFKA_SECURITY_PROTOCOL

    @property
    def is_configured(self) -> bool:
        return bool(self.bootstrap_servers)


@dataclass(frozen=True)
class KafkaValidationResult:
    """Result of validating a Kafka integration."""

    ok: bool
    detail: str


def kafka_is_available(sources: dict[str, dict]) -> bool:
    """Check if Kafka integration params are present in available sources."""
    return bool(sources.get("kafka", {}).get("connection_verified"))


def kafka_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Kafka connection params from resolved integrations.

    Credentials are resolved by detect_sources from the integration store,
    so the LLM never needs to supply bootstrap_servers or SASL credentials directly.
    """
    kf = sources.get("kafka", {})
    return {
        "bootstrap_servers": str(kf.get("bootstrap_servers", "")).strip(),
        "security_protocol": str(
            kf.get("security_protocol") or DEFAULT_KAFKA_SECURITY_PROTOCOL
        ).strip(),
        "sasl_mechanism": str(kf.get("sasl_mechanism", "")).strip(),
        "sasl_username": str(kf.get("sasl_username", "")).strip(),
        "sasl_password": str(kf.get("sasl_password", "")).strip(),
    }


def build_kafka_config(raw: dict[str, Any] | None) -> KafkaConfig:
    """Build a normalized Kafka config object from env/store data."""
    return KafkaConfig.model_validate(raw or {})


def kafka_config_from_env() -> KafkaConfig | None:
    """Load a Kafka config from env vars."""
    bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "").strip()
    if not bootstrap_servers:
        return None
    return build_kafka_config(
        {
            "bootstrap_servers": bootstrap_servers,
            "security_protocol": os.getenv(
                "KAFKA_SECURITY_PROTOCOL", DEFAULT_KAFKA_SECURITY_PROTOCOL
            ).strip(),
            "sasl_mechanism": os.getenv("KAFKA_SASL_MECHANISM", "").strip(),
            "sasl_username": os.getenv("KAFKA_SASL_USERNAME", "").strip(),
            "sasl_password": os.getenv("KAFKA_SASL_PASSWORD", "").strip(),
        }
    )


def _get_admin_client(config: KafkaConfig) -> Any:
    """Create a confluent_kafka AdminClient from config."""
    from confluent_kafka.admin import AdminClient

    conf: dict[str, Any] = {
        "bootstrap.servers": config.bootstrap_servers,
        "security.protocol": config.security_protocol,
        "socket.timeout.ms": int(config.timeout_seconds * 1000),
        "request.timeout.ms": int(config.timeout_seconds * 1000),
    }
    if config.sasl_mechanism:
        conf["sasl.mechanism"] = config.sasl_mechanism
    if config.sasl_username:
        conf["sasl.username"] = config.sasl_username
    if config.sasl_password:
        conf["sasl.password"] = config.sasl_password
    return AdminClient(conf)


def _get_consumer(config: KafkaConfig) -> Any:
    """Create a confluent_kafka Consumer for metadata queries."""
    from confluent_kafka import Consumer

    conf: dict[str, Any] = {
        "bootstrap.servers": config.bootstrap_servers,
        "security.protocol": config.security_protocol,
        "group.id": f"opensre-internal-{config.integration_id or 'readonly'}",
        "enable.auto.commit": False,
        "auto.offset.reset": "latest",
        "socket.timeout.ms": int(config.timeout_seconds * 1000),
        "request.timeout.ms": int(config.timeout_seconds * 1000),
    }
    if config.sasl_mechanism:
        conf["sasl.mechanism"] = config.sasl_mechanism
    if config.sasl_username:
        conf["sasl.username"] = config.sasl_username
    if config.sasl_password:
        conf["sasl.password"] = config.sasl_password
    return Consumer(conf)


def validate_kafka_config(config: KafkaConfig) -> KafkaValidationResult:
    """Validate Kafka connectivity by listing topics."""
    if not config.bootstrap_servers:
        return KafkaValidationResult(ok=False, detail="Kafka bootstrap_servers is required.")

    try:
        admin = _get_admin_client(config)
        metadata = admin.list_topics(timeout=config.timeout_seconds)
        topic_count = len(metadata.topics)
        broker_count = len(metadata.brokers)
        return KafkaValidationResult(
            ok=True,
            detail=(
                f"Connected to Kafka cluster with {broker_count} broker(s) "
                f"and {topic_count} topic(s)."
            ),
        )
    except Exception as err:  # noqa: BLE001
        return KafkaValidationResult(ok=False, detail=f"Kafka connection failed: {err}")


def get_topic_health(
    config: KafkaConfig,
    topic: str | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    """Retrieve topic partition health: offsets, replicas, ISR status.

    Read-only: uses cluster metadata. If topic is None, returns stats for
    all topics up to max_results.
    """
    if not config.is_configured:
        return {"source": "kafka", "available": False, "error": "Not configured."}

    effective_limit = min(limit or config.max_results, config.max_results)
    try:
        admin = _get_admin_client(config)
        if topic:
            metadata = admin.list_topics(topic=topic, timeout=config.timeout_seconds)
        else:
            metadata = admin.list_topics(timeout=config.timeout_seconds)

        topics: list[dict[str, Any]] = []
        for tname, tmeta in metadata.topics.items():
            if tname.startswith("__"):
                continue
            if len(topics) >= effective_limit:
                break
            partitions = []
            for pid, pmeta in tmeta.partitions.items():
                partitions.append(
                    {
                        "id": pid,
                        "leader": pmeta.leader,
                        "replicas": list(pmeta.replicas),
                        "isr": list(pmeta.isrs),
                        "under_replicated": len(pmeta.isrs) < len(pmeta.replicas),
                    }
                )
            topics.append(
                {
                    "name": tname,
                    "partition_count": len(tmeta.partitions),
                    "partitions": partitions,
                }
            )
        return {
            "source": "kafka",
            "available": True,
            "broker_count": len(metadata.brokers),
            "topics_returned": len(topics),
            "cluster_topic_count": len(metadata.topics),
            "topics": topics,
        }
    except Exception as err:  # noqa: BLE001
        return {"source": "kafka", "available": False, "error": str(err)}


def get_consumer_group_lag(
    config: KafkaConfig,
    group_id: str,
) -> dict[str, Any]:
    """Retrieve consumer group lag per partition.

    Read-only: queries committed offsets and compares to high watermarks.
    """
    if not config.is_configured:
        return {"source": "kafka", "available": False, "error": "Not configured."}

    try:
        from confluent_kafka import TopicPartition
        from confluent_kafka.admin import ConsumerGroupTopicPartitions

        admin = _get_admin_client(config)
        consumer = _get_consumer(config)

        try:
            # Get committed offsets for the group
            group_offsets = admin.list_consumer_group_offsets(
                [ConsumerGroupTopicPartitions(group_id)]
            )
            # Wait for the future to resolve
            group_result = None
            for group_future in group_offsets.values():
                group_result = group_future.result()

            # Build partition lag info
            lag_info = []
            for tp in group_result.topic_partitions if group_result else []:
                if tp.error:
                    continue
                # Get high watermark for this partition
                lo, hi = consumer.get_watermark_offsets(
                    TopicPartition(tp.topic, tp.partition),
                    timeout=config.timeout_seconds,
                )
                committed = tp.offset if tp.offset >= 0 else 0
                lag = max(0, hi - committed)
                lag_info.append(
                    {
                        "topic": tp.topic,
                        "partition": tp.partition,
                        "committed_offset": committed,
                        "high_watermark": hi,
                        "lag": lag,
                    }
                )

            total_lag = sum(p["lag"] for p in lag_info)
            return {
                "source": "kafka",
                "available": True,
                "group_id": group_id,
                "total_lag": total_lag,
                "partitions": lag_info,
            }
        finally:
            consumer.close()
    except Exception as err:  # noqa: BLE001
        return {"source": "kafka", "available": False, "error": str(err)}
