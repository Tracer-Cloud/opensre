"""RabbitMQ Node Health Tool."""

from typing import Any

from app.integrations.rabbitmq import (
    RabbitMQConfig,
    get_node_health,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_rabbitmq_node_health",
    description="Return per-node RabbitMQ resource utilization: memory used vs. limit (with alarm flag), disk free vs. limit (with alarm flag), file descriptors, sockets, erlang process usage, and cluster partition state. Essential for diagnosing backpressure, partitions, or node crashes.",
    source="rabbitmq",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Checking if a RabbitMQ node is under memory or disk pressure",
        "Detecting cluster network partitions between nodes",
        "Investigating file descriptor or socket exhaustion on a broker node",
    ],
    is_available=rabbitmq_is_available,
    extract_params=rabbitmq_extract_params,
)
def get_rabbitmq_node_health(
    host: str,
    username: str,
    password: str = "",
    management_port: int = 15672,
    vhost: str = "/",
    ssl: bool = False,
    verify_ssl: bool = True,
) -> dict[str, Any]:
    """Return per-node resource + partition diagnostics."""
    config = RabbitMQConfig(
        host=host,
        management_port=management_port,
        username=username,
        password=password,
        vhost=vhost,
        ssl=ssl,
        verify_ssl=verify_ssl,
    )
    return get_node_health(config)
