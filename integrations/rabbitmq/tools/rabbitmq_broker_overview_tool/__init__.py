"""RabbitMQ Broker Overview Tool."""

from typing import Any

from core.domain.types.evidence import record_evidence_entry
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.rabbitmq import (
    RabbitMQConfig,
    get_broker_overview,
    rabbitmq_extract_params,
    rabbitmq_is_available,
)


def _map_get_rabbitmq_broker_overview(
    evidence: dict[str, Any], output: dict[str, Any], _tool_input: dict[str, Any]
) -> None:
    """Cite the cluster snapshot, flagging any active alarm."""
    if not output.get("available"):
        return
    parts = [
        f"{output.get('queues', 0)} queue(s)",
        f"{output.get('messages_ready', 0)} ready",
        f"{output.get('messages_unacknowledged', 0)} unacked",
    ]
    alarms = output.get("alarms") or {}
    if alarms.get("ok") is False:
        parts.append(f"ALARM: {alarms.get('detail', 'active')}")
    record_evidence_entry(
        evidence,
        source="get_rabbitmq_broker_overview",
        label="RabbitMQ Broker Overview",
        summary=", ".join(parts),
    )


@tool(
    name="get_rabbitmq_broker_overview",
    description="Return a cluster-wide RabbitMQ overview: version, cluster name, total message counts, publish/deliver rates, queue/consumer/connection/channel totals, plus the alarm health-check status (memory / disk / file-descriptor alarms).",
    source="rabbitmq",
    side_effect_level=SideEffectLevel.READ_ONLY,
    concurrency_safe=True,
    surfaces=(ToolSurface.CHAT,),
    use_cases=[
        "Getting a quick cluster-wide health snapshot during an incident",
        "Checking if memory or disk alarms are active on the broker",
        "Comparing publish vs deliver rates to detect throughput imbalances",
    ],
    is_available=rabbitmq_is_available,
    injected_params=("host", "password", "username"),
    extract_params=rabbitmq_extract_params,
    evidence_mapper=_map_get_rabbitmq_broker_overview,
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
