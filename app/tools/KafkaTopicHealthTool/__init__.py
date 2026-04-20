"""Kafka Topic Health Tool."""

from typing import Any

from app.integrations.kafka import KafkaConfig, get_topic_health
from app.tools.tool_decorator import tool


@tool(
    name="get_kafka_topic_health",
    description="Retrieve topic partition health from a Kafka cluster, including replica status, ISR counts, and under-replicated partitions.",
    source="kafka",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking partition health during a consumer lag incident",
        "Identifying under-replicated partitions after a broker failure",
        "Reviewing topic metadata for capacity planning",
    ],
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
