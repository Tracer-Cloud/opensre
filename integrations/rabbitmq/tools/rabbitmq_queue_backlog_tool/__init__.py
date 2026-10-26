"""RabbitMQ Queue Backlog Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.rabbitmq import (
    RabbitMQConfig,
    get_queue_backlog,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)


def _map_get_rabbitmq_queue_backlog(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the largest backlog and how many queues have zero consumers.

    ``queues`` is only the top-``returned`` slice by backlog, not every queue
    on the broker (``total_queues``) -- a zero-consumer count over that slice
    must say so, since a queue with no consumers but low backlog would rank
    outside the slice and be invisible to a plain scan.
    """
    if not output.get("available"):
        return
    queues = output.get("queues") or []
    if not queues:
        return
    total_queues = output.get("total_queues", len(queues))
    returned = output.get("returned", len(queues))
    top = queues[0]
    top_backlog = top.get("messages_ready", 0) + top.get("messages_unacknowledged", 0)
    zero_consumer = sum(1 for q in queues if q.get("consumers", 0) == 0)
    summary = f"{total_queues} queue(s), top backlog {top_backlog} on '{top.get('name', '')}'"
    if zero_consumer:
        if returned < total_queues:
            summary += f", {zero_consumer} of the {returned} shown have zero consumers"
        else:
            summary += f", {zero_consumer} with zero consumers"
    record_evidence_entry(
        evidence,
        source="get_rabbitmq_queue_backlog",
        label="RabbitMQ Queue Backlog",
        summary=summary,
    )


@tool(
    name="get_rabbitmq_queue_backlog",
    description="List RabbitMQ queues ranked by backlog size (unacknowledged + ready messages). Reveals which queues are accumulating messages, their consumer count, and publish/deliver/ack rates.",
    source="rabbitmq",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Identifying queues with growing backlogs during an incident",
        "Checking if consumers are keeping up with publish rate",
        "Finding queues with zero consumers that are silently accumulating messages",
    ],
    is_available=rabbitmq_is_available,
    injected_params=("host", "password", "username"),
    extract_params=rabbitmq_extract_params,
    evidence_mapper=_map_get_rabbitmq_queue_backlog,
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
