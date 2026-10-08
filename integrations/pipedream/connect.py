"""Pipedream Connect as a remote MCP integration.

Workspace connections made in the OpenSRE app carry no vendor API token.
The app exports capability metadata and both the hosted agent and interactive
shell call an authenticated webapp proxy. Pipedream project credentials never
leave the webapp.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class PipedreamApp:
    """One app the workspace connected through Pipedream."""

    service: str
    app_slug: str
    account_id: str


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


def select_app(
    apps: tuple[PipedreamApp, ...],
    name: str | None,
    account_id: str | None = None,
) -> PipedreamApp | None:
    """Resolve a model-supplied app name to one connected account.

    A service or slug that matches more than one account is ambiguous.
    ``account_id``, or a name equal to one account id, selects that account.
    """
    requested = (name or "").strip().lower()
    account = (account_id or "").strip()
    if not apps:
        return None
    if account:
        scoped = [
            app
            for app in apps
            if app.account_id == account
            and (
                not requested
                or requested in {app.service.lower(), app.app_slug.lower(), app.account_id.lower()}
            )
        ]
        return scoped[0] if len(scoped) == 1 else None
    if not requested:
        return apps[0] if len(apps) == 1 else None
    by_id = [app for app in apps if app.account_id.lower() == requested]
    if len(by_id) == 1:
        return by_id[0]
    matches = [app for app in apps if requested in {app.service.lower(), app.app_slug.lower()}]
    if len(matches) == 1:
        return matches[0]
    return None


def classify(
    credentials: dict[str, Any], record_id: str
) -> tuple[dict[str, Any] | None, str | None]:
    """Classify a vault ``pipedream`` record into the tool source shape."""
    if str(credentials.get("provider") or "").strip() != "pipedream":
        return None, None
    access_mode = str(credentials.get("access_mode") or "").strip()
    apps = parse_apps(credentials.get("apps"))
    if access_mode != "webapp_proxy" or not apps:
        return None, None
    return {
        "provider": "pipedream",
        "access_mode": access_mode,
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


__all__ = [
    "PipedreamApp",
    "classify",
    "parse_apps",
    "select_app",
]
