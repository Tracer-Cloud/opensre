"""Grafana pre-investigation context source.

Fetches available Loki service names before investigation begins so the planner
can use the correct service_name on the first pass, eliminating the discovery loop
that would otherwise be needed.
"""

from __future__ import annotations

from app.agent.nodes.build_context.context_building import ContextSourceResult
from app.agent.nodes.build_context.utils import call_safe
from app.agent.state import InvestigationState
from app.agent.tools.tool_actions.grafana.grafana_actions import query_grafana_service_names


def build_context_grafana(state: InvestigationState) -> ContextSourceResult:
    """Fetch available Grafana Loki service names before investigation begins."""
    resolved = state.get("resolved_integrations") or {}
    grafana = resolved.get("grafana", {})
    endpoint = grafana.get("endpoint", "")
    api_key = grafana.get("api_key", "")

    if not endpoint or not api_key:
        return ContextSourceResult(data={"service_names": [], "connection_verified": False})

    outcome = call_safe(
        query_grafana_service_names,
        timeout=15.0,
        grafana_endpoint=endpoint,
        grafana_api_key=api_key,
    )

    if outcome.error or not outcome.result:
        return ContextSourceResult(
            data={
                "service_names": [],
                "connection_verified": False,
                "error": outcome.error or "No result",
            }
        )

    result = outcome.result
    return ContextSourceResult(
        data={
            "service_names": result.get("service_names", []),
            "connection_verified": result.get("available", False),
            "grafana_endpoint": endpoint,
        }
    )
