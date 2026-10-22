"""Kafka Topic Health Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.kafka import (
    KafkaConfig,
    get_topic_health,
    kafka_extract_params,
    kafka_is_available,
)


def _map_get_kafka_topic_health(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the surveyed topic count, and under-replicated partitions when any exist."""
    if not output.get("available"):
        return
    topics = output.get("topics") or []
    if not topics:
        return
    under_replicated = sum(
        1
        for topic in topics
        if isinstance(topic, dict)
        for partition in topic.get("partitions", [])
        if isinstance(partition, dict) and partition.get("under_replicated")
    )
    summary = f"{len(topics)} topic(s) surveyed"
    if under_replicated:
        summary += f", {under_replicated} under-replicated partition(s)"
    record_evidence_entry(
        evidence,
        source="get_kafka_topic_health",
        label="Kafka Topic Health",
        summary=summary,
    )


@tool(
    name="get_kafka_topic_health",
    description="Retrieve topic partition health from a Kafka cluster, including replica status, ISR counts, and under-replicated partitions.",
    source="kafka",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking partition health during a consumer lag incident",
        "Identifying under-replicated partitions after a broker failure",
        "Reviewing topic metadata for capacity planning",
    ],
    is_available=kafka_is_available,
    injected_params=("bootstrap_servers",),
    extract_params=kafka_extract_params,
    evidence_mapper=_map_get_kafka_topic_health,
)
def get_kafka_topic_health(
    bootstrap_servers: str,
    topic: str = "",
    security_protocol: str = "PLAINTEXT",
    sasl_mechanism: str = "",
    sasl_username: str = "",
    sasl_password: str = "",
    limit: int = 20,
) -> dict[str, Any]:
    """Fetch topic partition health from a Kafka cluster."""
    config = KafkaConfig(
        bootstrap_servers=bootstrap_servers,
        security_protocol=security_protocol,
        sasl_mechanism=sasl_mechanism,
        sasl_username=sasl_username,
        sasl_password=sasl_password,
    )
    return get_topic_health(config, topic=topic or None, limit=limit)
