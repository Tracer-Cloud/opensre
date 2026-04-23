"""Kafka Consumer Group Tool."""

from typing import Any

from app.integrations.kafka import (
    KafkaConfig,
    get_consumer_group_lag,
    kafka_extract_params,
    kafka_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_kafka_consumer_group_lag",
    description="Retrieve consumer group lag per partition from a Kafka cluster, showing committed offsets versus high watermarks.",
    source="kafka",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Diagnosing consumer lag causing processing delays",
        "Identifying stuck or slow consumers during an incident",
        "Checking consumer group health after a deployment",
    ],
    is_available=kafka_is_available,
    extract_params=kafka_extract_params,
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
