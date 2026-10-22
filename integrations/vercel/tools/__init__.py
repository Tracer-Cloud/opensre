# ======== from tools/vercel_deployment_status_tool/ ========

"""Vercel deployment status investigation tool."""

from __future__ import annotations

from typing import Any

from core.tool import BaseTool, SideEffectLevel
from core.tool_framework.utils import tool_unavailable
from integrations.vercel.client import make_vercel_client
from integrations.vercel.tools._evidence import (
    map_vercel_deployment_logs,
    map_vercel_deployment_status,
)

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


# ======== from tools/vercel_logs_tool/ ========

"""Vercel deployment logs investigation tool."""


_ERROR_KEYWORDS = ("error", "failed", "exception", "fatal", "crash", "panic", "unhandled")


class VercelLogsTool(BaseTool):
    """Pull build output and serverless function runtime logs for a Vercel deployment."""

    name = "vercel_deployment_logs"
    source = "vercel"
    side_effect_level = SideEffectLevel.READ_ONLY
    concurrency_safe = True
    evidence_mapper = map_vercel_deployment_logs
    description = (
        "Fetch build events and serverless function runtime logs for a specific Vercel deployment, "
        "useful for diagnosing build failures and runtime errors."
    )
    use_cases = [
        "Diagnosing why a Vercel build failed",
        "Fetching serverless function stdout/stderr for a deployment",
        "Correlating Vercel runtime errors with alerts from Datadog or Sentry",
        "Inspecting build output for dependency or compilation errors",
    ]
    requires = ["api_token", "deployment_id"]
    injected_params = ["api_token"]
    input_schema = {
        "type": "object",
        "properties": {
            "api_token": {"type": "string", "description": "Vercel API Bearer token"},
            "team_id": {"type": "string", "default": "", "description": "Optional Vercel team ID"},
            "project_id": {
                "type": "string",
                "default": "",
                "description": "Vercel project ID (scopes runtime logs to the project API)",
            },
            "deployment_id": {
                "type": "string",
                "description": "Vercel deployment ID (uid) to fetch logs for",
            },
            "include_runtime_logs": {
                "type": "boolean",
                "default": True,
                "description": "Whether to also fetch serverless function runtime logs",
            },
            "limit": {
                "type": "integer",
                "default": 100,
                "description": "Maximum number of log entries to fetch per source",
            },
        },
        "required": ["api_token", "deployment_id"],
    }
    outputs = {
        "events": "Build and runtime event stream for the deployment",
        "runtime_logs": "Serverless function stdout/stderr log entries",
        "error_events": "Subset of events containing error keywords",
        "deployment": "Deployment metadata including state and git info",
    }

    def is_available(self, sources: dict) -> bool:
        return bool(sources.get("vercel", {}).get("connection_verified"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        vercel = sources["vercel"]
        return {
            "api_token": vercel.get("api_token", ""),
            "team_id": vercel.get("team_id", ""),
            "project_id": vercel.get("project_id", ""),
            "deployment_id": vercel.get("deployment_id", ""),
            "include_runtime_logs": True,
            "limit": 100,
        }

    def run(
        self,
        api_token: str,
        deployment_id: str,
        team_id: str = "",
        project_id: str = "",
        include_runtime_logs: bool = True,
        limit: int = 100,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if not deployment_id:
            return tool_unavailable(
                "vercel",
                "deployment_id is required to fetch logs. Run vercel_deployment_status first to find a deployment ID.",
                events=[],
                runtime_logs=[],
                error_events=[],
                deployment={},
            )
        client = make_vercel_client(api_token, team_id)
        if client is None:
            return tool_unavailable(
                "vercel",
                "Vercel integration is not configured.",
                events=[],
                runtime_logs=[],
                error_events=[],
                deployment={},
            )

        with client:
            deployment_result = client.get_deployment(deployment_id)
            deployment = (
                deployment_result.get("deployment", {}) if deployment_result.get("success") else {}
            )
            project_id = str(project_id or _kwargs.get("project_id", "")).strip()

            events_result = client.get_deployment_events(deployment_id, limit=limit)
            events: list[dict[str, Any]] = []
            if events_result.get("success"):
                events = events_result.get("events", [])

            runtime_logs: list[dict[str, Any]] = []
            if include_runtime_logs:
                logs_result = client.get_runtime_logs(
                    deployment_id,
                    limit=limit,
                    project_id=project_id,
                )
                if logs_result.get("success"):
                    runtime_logs = logs_result.get("logs", [])

        error_events = [
            ev
            for ev in events
            if any(kw in str(ev.get("text", "")).lower() for kw in _ERROR_KEYWORDS)
        ]

        return {
            "source": "vercel",
            "available": True,
            "deployment_id": deployment_id,
            "deployment": deployment,
            "events": events,
            "error_events": error_events,
            "runtime_logs": runtime_logs,
            "total_events": len(events),
            "total_runtime_logs": len(runtime_logs),
        }


vercel_deployment_logs = VercelLogsTool()
