"""Vercel deployment logs investigation tool."""

from __future__ import annotations

from typing import Any

from app.integrations.clients.vercel import VercelClient, VercelConfig
from app.tools.base import BaseTool

_ERROR_KEYWORDS = ("error", "failed", "exception", "fatal", "crash", "panic", "unhandled")


def _make_client(api_token: str | None, team_id: str | None) -> VercelClient | None:
    token = (api_token or "").strip()
    if not token:
        return None
    try:
        return VercelClient(VercelConfig(api_token=token, team_id=team_id or ""))
    except Exception:
        return None


class VercelLogsTool(BaseTool):
    """Pull build output and serverless function runtime logs for a Vercel deployment."""

    name = "vercel_deployment_logs"
    source = "vercel"
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
    input_schema = {
        "type": "object",
        "properties": {
            "api_token": {"type": "string", "description": "Vercel API Bearer token"},
            "team_id": {"type": "string", "default": "", "description": "Optional Vercel team ID"},
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
        vercel = sources.get("vercel", {})
        return bool(vercel.get("connection_verified") and vercel.get("deployment_id"))

    def extract_params(self, sources: dict) -> dict[str, Any]:
        vercel = sources["vercel"]
        return {
            "api_token": vercel.get("api_token", ""),
            "team_id": vercel.get("team_id", ""),
            "deployment_id": vercel.get("deployment_id", ""),
            "include_runtime_logs": True,
            "limit": 100,
        }

    def run(
        self,
        api_token: str,
        deployment_id: str,
        team_id: str = "",
        include_runtime_logs: bool = True,
        limit: int = 100,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = _make_client(api_token, team_id)
        if client is None:
            return {
                "source": "vercel",
                "available": False,
                "error": "Vercel integration is not configured.",
                "events": [],
                "runtime_logs": [],
                "error_events": [],
                "deployment": {},
            }

        deployment_result = client.get_deployment(deployment_id)
        deployment = deployment_result.get("deployment", {}) if deployment_result.get("success") else {}

        events_result = client.get_deployment_events(deployment_id, limit=limit)
        events: list[dict[str, Any]] = []
        if events_result.get("success"):
            events = events_result.get("events", [])

        error_events = [
            ev for ev in events
            if any(kw in str(ev.get("text", "")).lower() for kw in _ERROR_KEYWORDS)
        ]

        runtime_logs: list[dict[str, Any]] = []
        if include_runtime_logs:
            logs_result = client.get_runtime_logs(deployment_id, limit=limit)
            if logs_result.get("success"):
                runtime_logs = logs_result.get("logs", [])

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
