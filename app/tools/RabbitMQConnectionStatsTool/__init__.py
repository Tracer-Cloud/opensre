"""RabbitMQ Connection Stats Tool."""

from typing import Any

from app.integrations.rabbitmq import (
    RabbitMQConfig,
    get_connection_stats,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_rabbitmq_connection_stats",
    description="List active RabbitMQ connections sorted by receive rate. Reports user, vhost, protocol, channel count, peer host/port, TLS status, and recv/send byte rates — helps spot connection exhaustion, slow consumers, or noisy publishers during an incident.",
    source="rabbitmq",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Investigating connection exhaustion or connection storms",
        "Identifying noisy publishers with high byte rates",
        "Checking if slow consumers are holding open idle connections",
    ],
    is_available=rabbitmq_is_available,
    extract_params=rabbitmq_extract_params,
)
def get_rabbitmq_connection_stats(
    host: str,
    username: str,
    password: str = "",
    management_port: int = 15672,
    vhost: str = "/",
    ssl: bool = False,
    verify_ssl: bool = True,
    max_results: int = 50,
) -> dict[str, Any]:
    """Return active connection metadata."""
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
    return get_connection_stats(config)
