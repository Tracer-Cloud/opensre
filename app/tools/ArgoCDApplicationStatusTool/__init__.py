"""Argo CD application status investigation tool."""

from __future__ import annotations

from typing import Any

from app.services.argocd import make_argocd_client
from app.tools.base import BaseTool


class ArgoCDApplicationStatusTool(BaseTool):
    """Fetch Argo CD application sync and health status."""

    name = "argocd_application_status"
    source = "argocd"
    description = (
        "Fetch Argo CD application sync status, health status, current revision, "
        "and recent deployment history."
    )
    use_cases = [
        "Checking whether a GitOps application is OutOfSync or Degraded",
        "Correlating an incident with a recent Argo CD deployment revision",
        "Listing visible Argo CD applications when an alert omits the application name",
    ]
    requires = ["base_url"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "Argo CD base URL"},
            "bearer_token": {"type": "string", "default": "", "description": "Argo CD API token"},
            "username": {"type": "string", "default": "", "description": "Argo CD username"},
            "password": {"type": "string", "default": "", "description": "Argo CD password"},
            "application_name": {
                "type": "string",
                "default": "",
                "description": "Application name",
            },
            "project": {"type": "string", "default": "", "description": "Optional Argo CD project"},
            "app_namespace": {
                "type": "string",
                "default": "",
                "description": "Optional app namespace",
            },
            "verify_ssl": {
                "type": "boolean",
                "default": True,
                "description": "Verify TLS certificates",
            },
        },
        "required": ["base_url"],
    }
    outputs = {
        "application": "Application status summary when application_name is provided",
        "applications": "Application list when application_name is omitted",
        "recent_history": "Recent Argo CD deployment history entries",
    }

    def is_available(self, sources: dict) -> bool:
        argocd = sources.get("argocd", {})
        return bool(
            argocd.get("connection_verified")
            and argocd.get("base_url")
            and (argocd.get("bearer_token") or (argocd.get("username") and argocd.get("password")))
        )

    def extract_params(self, sources: dict) -> dict[str, Any]:
        argocd = sources["argocd"]
        return {
            "base_url": argocd.get("base_url", ""),
            "bearer_token": argocd.get("bearer_token", ""),
            "username": argocd.get("username", ""),
            "password": argocd.get("password", ""),
            "application_name": argocd.get("application_name", ""),
            "project": argocd.get("project", ""),
            "app_namespace": argocd.get("app_namespace", ""),
            "verify_ssl": argocd.get("verify_ssl", True),
        }

    def run(
        self,
        base_url: str,
        bearer_token: str = "",
        username: str = "",
        password: str = "",
        application_name: str = "",
        project: str = "",
        app_namespace: str = "",
        verify_ssl: bool = True,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        client = make_argocd_client(
            base_url,
            bearer_token,
            username,
            password,
            project=project,
            app_namespace=app_namespace,
            verify_ssl=verify_ssl,
        )
        if client is None:
            return {
                "source": "argocd",
                "available": False,
                "error": "Argo CD integration is not configured (missing base_url or auth).",
                "application": {},
                "applications": [],
                "recent_history": [],
            }

        with client:
            if application_name:
                result = client.get_application_summary(
                    application_name,
                    project=project,
                    app_namespace=app_namespace,
                )
            else:
                result = client.list_applications(projects=[project] if project else None)

        if not result.get("success"):
            return {
                "source": "argocd",
                "available": False,
                "error": result.get("error", "unknown error"),
                "application": {},
                "applications": [],
                "recent_history": [],
            }

        return {
            "source": "argocd",
            "available": True,
            **result,
        }


argocd_application_status = ArgoCDApplicationStatusTool()
