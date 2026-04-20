"""RabbitMQ Consumer Health Tool."""

from typing import Any

from app.integrations.rabbitmq import (
    RabbitMQConfig,
    get_consumer_health,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_rabbitmq_consumer_health",
    description="List active RabbitMQ consumers with per-queue diagnostics: prefetch count, ack mode, active state, and the channel/connection each consumer is bound to. Helps identify stalled or missing consumers behind a backlog.",
    source="rabbitmq",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Diagnosing why a queue backlog is growing — are consumers connected?",
        "Checking prefetch counts to see if consumers are throttled",
        "Identifying stalled or inactive consumers on a specific queue",
    ],
    is_available=rabbitmq_is_available,
    extract_params=rabbitmq_extract_params,
)
def get_rabbitmq_consumer_health(
    host: str,
    username: str,
    password: str = "",
    management_port: int = 15672,
    vhost: str = "/",
    ssl: bool = False,
    verify_ssl: bool = True,
    max_results: int = 50,
) -> dict[str, Any]:
    """Return consumer-level diagnostics."""
    config = RabbitMQConfig(
        host=host,
        management_port=management_port,
        username=username,
        password=password,
        vhost=vhost,
        ssl=ssl,
        verify_ssl=verify_ssl,
        max_results=max_results,
    )
    return get_consumer_health(config)
