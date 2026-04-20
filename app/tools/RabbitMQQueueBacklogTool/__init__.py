"""RabbitMQ Queue Backlog Tool."""

from typing import Any

from app.integrations.rabbitmq import (
    RabbitMQConfig,
    get_queue_backlog,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_rabbitmq_queue_backlog",
    description="List RabbitMQ queues ranked by backlog size (unacknowledged + ready messages). Reveals which queues are accumulating messages, their consumer count, and publish/deliver/ack rates.",
    source="rabbitmq",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Identifying queues with growing backlogs during an incident",
        "Checking if consumers are keeping up with publish rate",
        "Finding queues with zero consumers that are silently accumulating messages",
    ],
    is_available=rabbitmq_is_available,
    extract_params=rabbitmq_extract_params,
)
def get_rabbitmq_queue_backlog(
    host: str,
    username: str,
    password: str = "",
    management_port: int = 15672,
    vhost: str = "/",
    ssl: bool = False,
    verify_ssl: bool = True,
    max_results: int = 50,
) -> dict[str, Any]:
    """Return the top queues by pending message count."""
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
    return get_queue_backlog(config)
