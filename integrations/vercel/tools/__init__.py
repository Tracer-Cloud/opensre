# ======== from tools/vercel_deployment_status_tool/ ========

"""Vercel deployment status investigation tool."""

from __future__ import annotations

from typing import Any

from core.tool import BaseTool, SideEffectLevel
from core.tool_framework.utils import tool_unavailable
from integrations.vercel.client import make_vercel_client
from integrations.vercel.tools._evidence import map_vercel_deployment_status
from integrations.vercel.tools.deployment_logs import VercelLogsTool, vercel_deployment_logs

_ERROR_STATES = {"ERROR", "CANCELED"}


class VercelDeploymentStatusTool(BaseTool):
    """Fetch recent deployment status for a Vercel project and surface failed deployments."""

    name = "vercel_deployment_status"
    source = "vercel"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = map_vercel_deployment_status
    description = (
        "Fetch recent Vercel deployments for a project and surface failed ones with error details, "
        "git commit info, and timestamps."
    )
    use_cases = [
        "Checking whether a recent Vercel deployment succeeded or failed",
        "Correlating a deployment failure with downstream errors in Datadog or Sentry",
        "Identifying which git commit triggered a broken deployment",
        "Listing recent deployment history for a Vercel project",
    ]
    requires = ["api_token"]
    injected_params = ["api_token"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_token": {"type": "string", "description": "Vercel API Bearer token"},
            "team_id": {"type": "string", "default": "", "description": "Optional Vercel team ID"},
            "project_id": {
                "type": "string",
                "default": "",
                "description": "Vercel project ID to scope the query",
            },
            "limit": {
                "type": "integer",
                "default": 10,
                "description": "Maximum number of deployments to fetch",
            },
            "state": {
                "type": "string",
                "default": "",
                "description": "Filter by state: READY, ERROR, BUILDING, or CANCELED",
            },
        },
        "required": ["api_token"],
    }
    outputs = {
        "deployments": "List of recent deployments with state, url, git metadata, and error details",
        "failed_deployments": "Subset of deployments in ERROR or CANCELED state",
        "total": "Total number of deployments returned",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("vercel", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        vercel = sources["vercel"]
        return {
            "api_token": vercel.get("api_token", ""),
            "team_id": vercel.get("team_id", ""),
            "project_id": vercel.get("project_id", ""),
            "limit": 10,
            "state": "",
        }

    def run(
        self,
        api_token: str,
        team_id: str = "",
        project_id: str = "",
        limit: int = 10,
        state: str = "",
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_vercel_client(api_token, team_id)
        if client is None:
            return tool_unavailable(
                "vercel",
                "Vercel integration is not configured.",
                deployments=[],
                failed_deployments=[],
                total=0,
            )

        with client:
            result = client.list_deployments(project_id=project_id, limit=limit, state=state)

        if not result.get("success"):
            return tool_unavailable(
                "vercel",
                result.get("error", "unknown error"),
                deployments=[],
                failed_deployments=[],
                total=0,
            )

        deployments = result.get("deployments", [])
        failed = [d for d in deployments if d.get("state", "").upper() in _ERROR_STATES]
        return {
            "source": "vercel",
            "available": True,
            "deployments": deployments,
            "failed_deployments": failed,
            "total": result.get("total", 0),
            "project_id": project_id,
        }


vercel_deployment_status = VercelDeploymentStatusTool()


__all__ = [
    "VercelDeploymentStatusTool",
    "VercelLogsTool",
    "vercel_deployment_logs",
    "vercel_deployment_status",
]
