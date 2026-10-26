"""RabbitMQ Consumer Health Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.rabbitmq import (
    RabbitMQConfig,
    get_consumer_health,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)


def _map_get_rabbitmq_consumer_health(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite consumer count, flagging any inactive consumers found.

    ``consumers`` is only the first-``returned`` slice of the broker's
    consumer list, not every consumer (``total_consumers``) -- an inactive
    count over that slice must say so, since it can't speak for consumers
    outside the slice.
    """
    if not output.get("available"):
        return
    consumers = output.get("consumers") or []
    if not consumers:
        return
    total_consumers = output.get("total_consumers", len(consumers))
    returned = output.get("returned", len(consumers))
    inactive = sum(1 for c in consumers if not c.get("active", True))
    summary = f"{total_consumers} consumer(s)"
    if inactive:
        if returned < total_consumers:
            summary += f", {inactive} of the {returned} shown are inactive"
        else:
            summary += f", {inactive} inactive"
    record_evidence_entry(
        evidence,
        source="get_rabbitmq_consumer_health",
        label="RabbitMQ Consumer Health",
        summary=summary,
    )


@tool(
    name="get_rabbitmq_consumer_health",
    description="List active RabbitMQ consumers with per-queue diagnostics: prefetch count, ack mode, active state, and the channel/connection each consumer is bound to. Helps identify stalled or missing consumers behind a backlog.",
    source="rabbitmq",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Diagnosing why a queue backlog is growing — are consumers connected?",
        "Checking prefetch counts to see if consumers are throttled",
        "Identifying stalled or inactive consumers on a specific queue",
    ],
    is_available=rabbitmq_is_available,
    injected_params=("host", "password", "username"),
    extract_params=rabbitmq_extract_params,
    evidence_mapper=_map_get_rabbitmq_consumer_health,
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
