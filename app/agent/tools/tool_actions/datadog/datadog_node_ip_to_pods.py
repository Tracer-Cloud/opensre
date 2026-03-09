"""Datadog action: resolve a node IP to the pods running on that node.

Accepts a node IP from an alert, queries the Datadog Infrastructure API,
and returns all pods observed on that node with their current status.

Output is intended to feed directly into pod log retrieval tools.
"""

from __future__ import annotations

from typing import Any

from app.agent.tools.tool_actions.datadog._client import resolve_datadog_client


def get_pods_on_node(
    node_ip: str,
    time_range_minutes: int = 60,
    limit: int = 200,
    api_key: str | None = None,
    app_key: str | None = None,
    site: str = "datadoghq.com",
    **_kwargs: Any,
) -> dict:
    """Resolve a node IP address to all pods running on that node via Datadog.

    Accepts a node IP from an alert (e.g. from a Datadog Infrastructure alert),
    queries Datadog log telemetry tagged with that node IP, and returns the unique
    set of pods observed on that node with their last-known status.

    Output feeds directly into pod log retrieval tools for further investigation.

    Args:
        node_ip: The IP address of the node (e.g. "10.0.1.42")
        time_range_minutes: How far back to search for pod activity (default 60)
        limit: Max log events to scan when building the pod list (default 200)
        api_key: Datadog API key
        app_key: Datadog application key
        site: Datadog site (e.g., datadoghq.com, datadoghq.eu)

    Returns:
        source: "datadog_node_ip_to_pods"
        available: bool
        node_ip: The queried node IP
        pods: List of pod dicts, each with:
            - pod_name: str
            - namespace: str | None
            - container: str | None
            - node_ip: str
            - node_name: str | None
            - exit_code: str | None
            - status: "running" | "failed"
        total: Number of unique pods found
    """
    if not node_ip or not node_ip.strip():
        return {
            "source": "datadog_node_ip_to_pods",
            "available": False,
            "error": "node_ip is required",
            "pods": [],
        }

    client = resolve_datadog_client(api_key, app_key, site)

    if not client or not client.is_configured:
        return {
            "source": "datadog_node_ip_to_pods",
            "available": False,
            "error": "Datadog integration not configured",
            "pods": [],
        }

    result = client.get_pods_on_node(
        node_ip=node_ip,
        time_range_minutes=time_range_minutes,
        limit=limit,
    )

    if not result.get("success"):
        return {
            "source": "datadog_node_ip_to_pods",
            "available": False,
            "error": result.get("error", "Unknown error"),
            "node_ip": node_ip,
            "pods": [],
        }

    return {
        "source": "datadog_node_ip_to_pods",
        "available": True,
        "node_ip": node_ip,
        "pods": result.get("pods", []),
        "total": result.get("total", 0),
    }


