"""Kafka Topic Health Tool."""

from typing import Any

from app.integrations.kafka import (
    KafkaConfig,
    get_topic_health,
    kafka_extract_params,
    kafka_is_available,
)
from app.tools.tool_decorator import tool


def _get_kafka_topic_health_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    """Extract Kafka connection and topic parameters from sources."""
    params = kafka_extract_params(sources)
    params["topic"] = str(sources.get("kafka", {}).get("topic", "")).strip()
    return params


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
    is_available=kafka_is_available,
    extract_params=_get_kafka_topic_health_extract_params,
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
    try:
        return get_topic_health(config, topic=topic or None, limit=limit)
    except Exception as err:  # noqa: BLE001
        return {"source": "kafka", "available": False, "error": str(err)}
