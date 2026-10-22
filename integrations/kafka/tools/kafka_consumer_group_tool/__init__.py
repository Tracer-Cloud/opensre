"""Kafka Consumer Group Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.kafka import (
    KafkaConfig,
    get_consumer_group_lag,
    kafka_extract_params,
    kafka_is_available,
)


def _map_get_kafka_consumer_group_lag(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the consumer group's total lag across the partitions returned."""
    if not output.get("available"):
        return
    partitions = output.get("partitions") or []
    if not partitions:
        return
    total_lag = output.get("total_lag")
    summary = f"{len(partitions)} partition(s)"
    if total_lag is not None:
        summary += f", total lag {total_lag}"
    group_id = output.get("group_id")
    if group_id:
        summary = f"{group_id}: {summary}"
    record_evidence_entry(
        evidence,
        source="get_kafka_consumer_group_lag",
        label="Kafka Consumer Group Lag",
        summary=summary,
    )


@tool(
    name="get_kafka_consumer_group_lag",
    description="Retrieve consumer group lag per partition from a Kafka cluster, showing committed offsets versus high watermarks.",
    source="kafka",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Diagnosing consumer lag causing processing delays",
        "Identifying stuck or slow consumers during an incident",
        "Checking consumer group health after a deployment",
    ],
    is_available=kafka_is_available,
    injected_params=("bootstrap_servers",),
    extract_params=kafka_extract_params,
    evidence_mapper=_map_get_kafka_consumer_group_lag,
)
def get_kafka_consumer_group_lag(
    bootstrap_servers: str,
    group_id: str,
    security_protocol: str = "PLAINTEXT",
    sasl_mechanism: str = "",
    sasl_username: str = "",
    sasl_password: str = "",
) -> dict[str, Any]:
    """Fetch consumer group lag from a Kafka cluster."""
    config = KafkaConfig(
        bootstrap_servers=bootstrap_servers,
        security_protocol=security_protocol,
        sasl_mechanism=sasl_mechanism,
        sasl_username=sasl_username,
        sasl_password=sasl_password,
    )
    return get_consumer_group_lag(config, group_id=group_id)
