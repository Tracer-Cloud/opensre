"""Pipedream Connect as a remote MCP integration.

Workspace connections made in the OpenSRE app carry no vendor API token.
The app exports one ``pipedream`` record — a short-lived Pipedream access
token plus the connected apps — and both the hosted agent and the interactive
shell classify it here, then call ``https://remote.mcp.pipedream.net``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from integrations.mcp_transport import McpTransportMode

PIPEDREAM_MCP_URL = "https://remote.mcp.pipedream.net/v3"
_DEFAULT_TIMEOUT_SECONDS = 30.0


@dataclass(frozen=True)
class PipedreamApp:
    """One app the workspace connected through Pipedream."""

    service: str
    app_slug: str
    account_id: str


@dataclass(frozen=True)
class PipedreamMcpConfig:
    """Connection settings for one Pipedream app's remote MCP session."""

    url: str
    auth_token: str
    project_id: str
    environment: str
    external_user_id: str
    app_slug: str
    account_id: str
    timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS

    @property
    def mode(self) -> McpTransportMode:
        return McpTransportMode.STREAMABLE_HTTP

    @property
    def command(self) -> str:
        return ""

    @property
    def args(self) -> tuple[str, ...]:
        return ()

    @property
    def session_url(self) -> str:
        return self.url

    @property
    def request_headers(self) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.auth_token}",
            "x-pd-project-id": self.project_id,
            "x-pd-environment": self.environment,
            "x-pd-external-user-id": self.external_user_id,
            "x-pd-app-slug": self.app_slug,
        }
        if self.account_id:
            headers["x-pd-account-id"] = self.account_id
        return headers


def parse_apps(value: object) -> tuple[PipedreamApp, ...]:
    """Parse the vault's ``apps`` JSON string (or an already-decoded list)."""
    raw: object = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        try:
            raw = json.loads(text)
        except json.JSONDecodeError:
            return ()
    if not isinstance(raw, list):
        return ()
    apps: list[PipedreamApp] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        service = str(item.get("service") or "").strip()
        app_slug = str(item.get("app_slug") or item.get("app") or "").strip()
        account_id = str(item.get("account_id") or "").strip()
        if not service or not app_slug:
            continue
        apps.append(PipedreamApp(service=service, app_slug=app_slug, account_id=account_id))
    return tuple(apps)


def select_app(apps: tuple[PipedreamApp, ...], name: str | None) -> PipedreamApp | None:
    """Resolve a model-supplied app name to one connected account."""
    requested = (name or "").strip().lower()
    if not apps:
        return None
    if not requested:
        return apps[0] if len(apps) == 1 else None
    for app in apps:
        if requested in {app.service.lower(), app.app_slug.lower()}:
            return app
    return None


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Classify a vault ``pipedream`` record into the tool source shape."""
    if str(credentials.get("provider") or "").strip() != "pipedream":
        return None, None
    token = str(credentials.get("auth_token") or "").strip()
    url = str(credentials.get("url") or "").strip() or PIPEDREAM_MCP_URL
    apps = parse_apps(credentials.get("apps"))
    project_id = str(credentials.get("project_id") or "").strip()
    environment = str(credentials.get("environment") or "").strip()
    external_user_id = str(credentials.get("external_user_id") or "").strip()
    if not token or not apps or not project_id or not environment or not external_user_id:
        return None, None
    return {
        "provider": "pipedream",
        "url": url,
        "mode": "streamable-http",
        "auth_token": token,
        "token_expires_at": str(credentials.get("token_expires_at") or "").strip(),
        "project_id": project_id,
        "environment": environment,
        "external_user_id": external_user_id,
        "apps": [
            {
                "service": app.service,
                "app_slug": app.app_slug,
                "account_id": app.account_id,
            }
            for app in apps
        ],
        "integration_id": record_id,
    }, "pipedream"


def config_for_app(source: dict[str, Any], app: PipedreamApp) -> PipedreamMcpConfig | None:
    """Build an MCP client config for one connected app."""
    token = str(source.get("auth_token") or "").strip()
    project_id = str(source.get("project_id") or "").strip()
    environment = str(source.get("environment") or "").strip()
    external_user_id = str(source.get("external_user_id") or "").strip()
    url = str(source.get("url") or "").strip() or PIPEDREAM_MCP_URL
    if not token or not project_id or not environment or not external_user_id:
        return None
    return PipedreamMcpConfig(
        url=url,
        auth_token=token,
        project_id=project_id,
        environment=environment,
        external_user_id=external_user_id,
        app_slug=app.app_slug,
        account_id=app.account_id,
    )


__all__ = [
    "PIPEDREAM_MCP_URL",
    "PipedreamApp",
    "PipedreamMcpConfig",
    "classify",
    "config_for_app",
    "parse_apps",
    "select_app",
]
