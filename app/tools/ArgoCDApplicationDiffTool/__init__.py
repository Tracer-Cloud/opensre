"""Argo CD application diff/drift investigation tool."""

from __future__ import annotations

from typing import Any

from app.services.argocd import make_argocd_client
from app.tools.base import BaseTool


class ArgoCDApplicationDiffTool(BaseTool):
    """Fetch Argo CD server-side diff data for an application."""

    name = "argocd_application_diff"
    source = "argocd"
    description = (
        "Fetch Argo CD server-side diff output and report whether live cluster state "
        "has drifted from the desired GitOps state."
    )
    use_cases = [
        "Detecting GitOps drift during an incident",
        "Checking whether an OutOfSync application has Kubernetes object diffs",
        "Correlating deployment drift with application health degradation",
    ]
    requires = ["base_url", "application_name"]
    input_schema = {
        "type": "object",
        "properties": {
            "base_url": {"type": "string", "description": "Argo CD base URL"},
            "bearer_token": {"type": "string", "default": "", "description": "Argo CD API token"},
            "username": {"type": "string", "default": "", "description": "Argo CD username"},
            "password": {"type": "string", "default": "", "description": "Argo CD password"},
            "application_name": {"type": "string", "description": "Application name"},
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
        "required": ["base_url", "application_name"],
    }
    outputs = {
        "drift_detected": "True when Argo CD reports one or more object diffs",
        "diffs": "Sanitized server-side diff records",
        "diff_count": "Number of diff records returned",
    }

    def is_available(self, sources: dict) -> bool:
        argocd = sources.get("argocd", {})
        return bool(
            argocd.get("connection_verified")
            and argocd.get("base_url")
            and argocd.get("application_name")
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
        application_name: str,
        bearer_token: str = "",
        username: str = "",
        password: str = "",
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
                "application_name": application_name,
                "drift_detected": False,
                "diffs": [],
                "diff_count": 0,
            }

        with client:
            result = client.get_application_diff(
                application_name,
                project=project,
                app_namespace=app_namespace,
            )

        if not result.get("success"):
            return {
                "source": "argocd",
                "available": False,
                "error": result.get("error", "unknown error"),
                "application_name": application_name,
                "drift_detected": False,
                "diffs": [],
                "diff_count": 0,
            }

        return {
            "source": "argocd",
            "available": True,
            **result,
        }


argocd_application_diff = ArgoCDApplicationDiffTool()
