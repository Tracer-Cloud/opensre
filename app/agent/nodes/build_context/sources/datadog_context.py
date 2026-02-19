"""Datadog pre-investigation context source.

Fetches Datadog monitor state before investigation begins so the planner
has monitor configuration and current alert states on the first pass.
"""

from __future__ import annotations

from app.agent.nodes.build_context.context_building import ContextSourceResult
from app.agent.nodes.build_context.utils import call_safe
from app.agent.state import InvestigationState
from app.agent.tools.tool_actions.datadog.datadog_actions import query_datadog_monitors


def build_context_datadog(state: InvestigationState) -> ContextSourceResult:
    """Fetch Datadog monitor state before investigation begins."""
    resolved = state.get("resolved_integrations") or {}
    datadog = resolved.get("datadog", {})
    api_key = datadog.get("api_key", "")
    app_key = datadog.get("app_key", "")
    site = datadog.get("site", "datadoghq.com")

    if not api_key or not app_key:
        return ContextSourceResult(data={"monitors": [], "connection_verified": False})

    pipeline_name = state.get("pipeline_name") or ""
    monitor_query = f"tag:pipeline:{pipeline_name}" if pipeline_name else None

    outcome = call_safe(
        query_datadog_monitors,
        timeout=15.0,
        query=monitor_query,
        api_key=api_key,
        app_key=app_key,
        site=site,
    )

    if outcome.error or not outcome.result:
        return ContextSourceResult(
            data={
                "monitors": [],
                "connection_verified": False,
                "error": outcome.error or "No result",
            }
        )

    result = outcome.result
    return ContextSourceResult(
        data={
            "monitors": result.get("monitors", []),
            "total": result.get("total", 0),
            "connection_verified": result.get("available", False),
        }
    )
