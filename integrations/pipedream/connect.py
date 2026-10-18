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

#: ``scope`` of an app the acting member connected for themself (their Gmail,
#: Drive, documents). The webapp serves it only for that member's turns.
PERSONAL_SCOPE = "personal"


@dataclass(frozen=True)
class PipedreamApp:
    """One account connected through Pipedream: the workspace's or the acting member's."""

    service: str
    app_slug: str
    account_id: str
    #: True for the acting member's own account; the proxy must name them as actor.
    personal: bool = False
    #: What the provider calls the account, such as the Gmail address; may be empty.
    account_name: str = ""

    def label(self) -> str:
        """How the account is listed to the model when it must pick one."""
        name = f"{self.service} {self.account_name}" if self.account_name else self.service
        scope = ", yours" if self.personal else ""
        return f"{name} ({self.account_id}{scope})"


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
        apps.append(
            PipedreamApp(
                service=service,
                app_slug=app_slug,
                account_id=account_id,
                personal=str(item.get("scope") or "").strip() == PERSONAL_SCOPE,
                account_name=str(item.get("account_name") or "").strip(),
            )
        )
    return tuple(apps)


def _app_payload(app: PipedreamApp) -> dict[str, str]:
    payload = {"service": app.service, "app_slug": app.app_slug, "account_id": app.account_id}
    if app.personal:
        payload["scope"] = PERSONAL_SCOPE
    if app.account_name:
        payload["account_name"] = app.account_name
    return payload


def merge_member_apps(
    records: list[dict[str, Any]], member_records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Fold the acting member's personal Pipedream apps into the workspace record.

    Classification keeps one record per service, so a second ``pipedream``
    record would be ignored. The member's apps join the workspace record
    (or form one) marked personal, for this turn only; ``records`` is not
    modified. Returns ``records`` unchanged when the member has none.
    """
    personal: list[PipedreamApp] = []
    for record in member_records:
        credentials = record.get("credentials")
        if str(record.get("service") or "").strip().lower() != "pipedream":
            continue
        if not isinstance(credentials, dict):
            continue
        if str(credentials.get("scope") or "").strip() != PERSONAL_SCOPE:
            continue
        personal.extend(
            PipedreamApp(
                service=app.service,
                app_slug=app.app_slug,
                account_id=app.account_id,
                personal=True,
                account_name=app.account_name,
            )
            for app in parse_apps(credentials.get("apps"))
        )
    if not personal:
        return records

    workspace_index = next(
        (
            index
            for index, record in enumerate(records)
            if str(record.get("service") or "").strip().lower() == "pipedream"
        ),
        None,
    )
    workspace = records[workspace_index] if workspace_index is not None else None
    workspace_credentials = (
        dict(workspace.get("credentials") or {}) if isinstance(workspace, dict) else {}
    )
    known = {app.account_id for app in parse_apps(workspace_credentials.get("apps"))}
    apps = [
        *parse_apps(workspace_credentials.get("apps")),
        *(app for app in personal if app.account_id not in known),
    ]
    credentials = {
        **workspace_credentials,
        "provider": "pipedream",
        "access_mode": "webapp_proxy",
        "apps": json.dumps([_app_payload(app) for app in apps]),
    }
    merged: dict[str, Any] = {
        **(workspace or {"id": "pipedream:member", "service": "pipedream", "name": "default"}),
        "status": "active",
        "credentials": credentials,
    }
    instances = workspace.get("instances") if isinstance(workspace, dict) else None
    first = instances[0] if isinstance(instances, list) and instances else {}
    tags = first.get("tags") if isinstance(first, dict) else None
    merged["instances"] = [
        {
            "name": str(merged.get("name") or "default"),
            "tags": dict(tags) if isinstance(tags, dict) else {},
            "credentials": credentials,
        }
    ]
    if workspace_index is None:
        return [*records, merged]
    return [*records[:workspace_index], merged, *records[workspace_index + 1 :]]


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
        "apps": [_app_payload(app) for app in apps],
        "integration_id": record_id,
    }, "pipedream"


__all__ = [
    "PERSONAL_SCOPE",
    "PipedreamApp",
    "classify",
    "merge_member_apps",
    "parse_apps",
    "select_app",
]
