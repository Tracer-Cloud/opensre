"""RabbitMQ Broker Overview Tool."""

from typing import Any

from app.integrations.rabbitmq import (
    RabbitMQConfig,
    get_broker_overview,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)
from app.tools.tool_decorator import tool


@tool(
    name="get_rabbitmq_broker_overview",
    description="Return a cluster-wide RabbitMQ overview: version, cluster name, total message counts, publish/deliver rates, queue/consumer/connection/channel totals, plus the alarm health-check status (memory / disk / file-descriptor alarms).",
    source="rabbitmq",
    surfaces=("investigation", "chat"),
    use_cases=[
        "Getting a quick cluster-wide health snapshot during an incident",
        "Checking if memory or disk alarms are active on the broker",
        "Comparing publish vs deliver rates to detect throughput imbalances",
    ],
    is_available=rabbitmq_is_available,
    extract_params=rabbitmq_extract_params,
)
def get_rabbitmq_broker_overview(
    host: str,
    username: str,
    password: str = "",
    management_port: int = 15672,
    vhost: str = "/",
    ssl: bool = False,
    verify_ssl: bool = True,
) -> dict[str, Any]:
    """Return cluster-wide broker overview + alarm state."""
    config = RabbitMQConfig(
        host=host,
        management_port=management_port,
        username=username,
        password=password,
        vhost=vhost,
        ssl=ssl,
        verify_ssl=verify_ssl,
    )
    return get_broker_overview(config)
