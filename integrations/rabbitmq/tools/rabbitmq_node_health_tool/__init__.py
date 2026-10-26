"""RabbitMQ Node Health Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.rabbitmq import (
    RabbitMQConfig,
    get_node_health,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)


def _map_get_rabbitmq_node_health(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite node count, flagging partitions and any active mem/disk alarms."""
    if not output.get("available"):
        return
    nodes = output.get("nodes") or []
    if not nodes:
        return
    alarmed = [n.get("name", "") for n in nodes if n.get("mem_alarm") or n.get("disk_free_alarm")]
    summary = f"{output.get('node_count', len(nodes))} node(s)"
    if output.get("any_partitioned"):
        summary += ", cluster partitioned"
    if alarmed:
        summary += f", alarm on {', '.join(alarmed)}"
    record_evidence_entry(
        evidence,
        source="get_rabbitmq_node_health",
        label="RabbitMQ Node Health",
        summary=summary,
    )


@tool(
    name="get_rabbitmq_node_health",
    description="Return per-node RabbitMQ resource utilization: memory used vs. limit (with alarm flag), disk free vs. limit (with alarm flag), file descriptors, sockets, erlang process usage, and cluster partition state. Essential for diagnosing backpressure, partitions, or node crashes.",
    source="rabbitmq",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Checking if a RabbitMQ node is under memory or disk pressure",
        "Detecting cluster network partitions between nodes",
        "Investigating file descriptor or socket exhaustion on a broker node",
    ],
    is_available=rabbitmq_is_available,
    injected_params=("host", "password", "username"),
    extract_params=rabbitmq_extract_params,
    evidence_mapper=_map_get_rabbitmq_node_health,
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
