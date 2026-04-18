"""Datadog tool: resolve a node IP to the pods running on that node."""

from __future__ import annotations

from typing import Any

from app.tools.DataDogLogsTool import _dd_creds
from app.tools.DataDogLogsTool._client import make_client, unavailable
from app.tools.tool_decorator import tool


def _node_pods_is_available(sources: dict[str, dict]) -> bool:
    dd = sources.get("datadog", {})
    return bool(dd.get("connection_verified") and dd.get("node_ip"))


def _node_pods_extract_params(sources: dict[str, dict]) -> dict[str, Any]:
    dd = sources["datadog"]
    return {
        "node_ip": dd.get("node_ip", ""),
        "time_range_minutes": dd.get("time_range_minutes", 60),
        **_dd_creds(dd),
    }


@tool(
    name="get_pods_on_node",
    source="datadog",
    description="Resolve a node IP address to all pods running on that node via Datadog.",
    use_cases=[
        "Mapping a node IP from an infrastructure alert to specific pods",
        "Discovering what pods were running on a failed node",
        "Feeding pod names into log retrieval tools for further investigation",
    ],
    requires=[],
    input_schema={
        "type": "object",
        "properties": {
            "node_ip": {"type": "string", "description": "The IP address of the node (e.g. '10.0.1.42')"},
            "time_range_minutes": {"type": "integer", "default": 60},
            "limit": {"type": "integer", "default": 200},
            "api_key": {"type": "string"},
            "app_key": {"type": "string"},
            "site": {"type": "string", "default": "datadoghq.com"},
        },
        "required": ["node_ip"],
    },
    is_available=_node_pods_is_available,
    extract_params=_node_pods_extract_params,
)
def get_pods_on_node(
    node_ip: str,
    time_range_minutes: int = 60,
    limit: int = 200,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    **_kwargs: Any,
) -> dict[str, Any]:
    """Resolve a node IP address to all pods running on that node via Datadog."""
    if not node_ip or not node_ip.strip():
        return unavailable("datadog_node_ip_to_pods", "pods", "node_ip is required")

    client = make_client(api_key, app_key, site)
    if not client:
        return unavailable("datadog_node_ip_to_pods", "pods", "Datadog integration not configured")

    result = client.get_pods_on_node(node_ip=node_ip, time_range_minutes=time_range_minutes, limit=limit)
    if not result.get("success"):
        return unavailable(
            "datadog_node_ip_to_pods", "pods", result.get("error", "Unknown error"), node_ip=node_ip
        )

    return {
        "source": "datadog_node_ip_to_pods",
        "available": True,
        "node_ip": node_ip,
        "pods": result.get("pods", []),
        "total": result.get("total", 0),
    }
