"""RabbitMQ Connection Stats Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.rabbitmq import (
    RabbitMQConfig,
    get_connection_stats,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)


def _map_get_rabbitmq_connection_stats(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite connection count for the vhost against the broker-wide total."""
    if not output.get("available"):
        return
    connections = output.get("connections") or []
    if not connections:
        return
    summary = (
        f"{output.get('vhost_connections', len(connections))} connection(s) in vhost "
        f"(of {output.get('broker_total_connections', 0)} broker-wide)"
    )
    record_evidence_entry(
        evidence,
        source="get_rabbitmq_connection_stats",
        label="RabbitMQ Connection Stats",
        summary=summary,
    )


@tool(
    name="get_rabbitmq_connection_stats",
    description="List active RabbitMQ connections sorted by receive rate. Reports user, vhost, protocol, channel count, peer host/port, TLS status, and recv/send byte rates — helps spot connection exhaustion, slow consumers, or noisy publishers during an incident.",
    source="rabbitmq",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Investigating connection exhaustion or connection storms",
        "Identifying noisy publishers with high byte rates",
        "Checking if slow consumers are holding open idle connections",
    ],
    is_available=rabbitmq_is_available,
    injected_params=("host", "password", "username"),
    extract_params=rabbitmq_extract_params,
    evidence_mapper=_map_get_rabbitmq_connection_stats,
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
