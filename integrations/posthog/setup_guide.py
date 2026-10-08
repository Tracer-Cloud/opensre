"""Guide PostHog region, personal-key, and project selection."""

from __future__ import annotations

from collections.abc import Mapping
from http import HTTPStatus
from urllib.parse import urlsplit

import httpx

from integrations.posthog.config import PostHogConfig
from integrations.setup_flow import SetupUI


def _app_host(value: str) -> str:
    """Use the app host for personal-key requests, including old capture-host defaults."""
    host = value.rstrip("/")
    return {
        "https://us.i.posthog.com": "https://us.posthog.com",
        "https://eu.i.posthog.com": "https://eu.posthog.com",
    }.get(host, host)


def _host(ui: SetupUI, saved: Mapping[str, str]) -> str:
    ui.say("Choose the region shown in your PostHog browser address.")
    choices = [
        ("https://us.posthog.com", "US Cloud (us.posthog.com)"),
        ("https://eu.posthog.com", "EU Cloud (eu.posthog.com)"),
        ("custom", "Self-hosted / another address"),
    ]
    existing = _app_host(saved.get("base_url", ""))
    if existing:
        choices.insert(0, (existing, f"Keep {existing}"))
    host = ui.choose("Where is your PostHog account?", choices)
    if host != "custom":
        return host
    ui.say("Copy the address of your PostHog app, for example https://analytics.example.com.")
    while True:
        host = ui.value("PostHog app URL").rstrip("/")
        parsed = urlsplit(host)
        if (
            parsed.scheme in {"https", "http"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            and not parsed.query
            and not parsed.fragment
            and not parsed.path
        ):
            return host
        ui.say("Use an http:// or https:// app address without a path, query, or credentials.")


def _projects(config: PostHogConfig) -> tuple[list[tuple[str, str]], str]:
    """List one bounded page without forwarding a credential to pagination or redirect URLs."""
    try:
        response = httpx.get(
            f"{config.api_base_url}/api/projects/",
            headers=config.auth_headers,
            params={"limit": 100},
            timeout=10,
            follow_redirects=False,
        )
        if response.status_code in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
            return (
                [],
                "Check the key's project:read permission and access to your project, then retry.",
            )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        return [], "Could not list projects. Check your region, connection, and personal API key."
    if not isinstance(payload, dict) or not isinstance(payload.get("results"), list):
        return [], "PostHog returned an unexpected project list. Try again."
    choices: dict[str, str] = {}
    for project in payload["results"][:100]:
        if isinstance(project, dict) and isinstance(project.get("id"), int):
            project_id = str(project["id"])
            choices[project_id] = f"{project.get('name') or 'Project'} ({project_id})"
    note = (
        "Showing the first 100 projects. Use a project ID if yours isn't listed."
        if payload.get("next")
        else ""
    )
    return list(choices.items()), note


def guide_posthog(ui: SetupUI, saved: Mapping[str, str]) -> dict[str, str]:
    """Collect a personal key with instructions, then discover the project ID."""
    ui.step("one", "Connect your PostHog account")
    host = _host(ui, saved)
    ui.say(f"Open {host}/settings/user-api-keys")
    ui.say(
        "Create a personal API key named OpenSRE, grant read access to your project, then copy it."
    )
    ui.say(
        "Include project:read for setup and query:read for analytics queries. Use the personal key (phx_), not the project capture key (phc_)."
    )
    same_host = host == _app_host(saved.get("base_url", ""))
    key = ui.value(
        "Paste your personal API key (hidden)",
        default=saved.get("personal_api_key", "") if same_host else "",
        secret=True,
    )
    ui.step("two", "Choose your project")
    while True:
        ui.say("Finding your projects… (up to 10 seconds)")
        projects, note = _projects(PostHogConfig(base_url=host, personal_api_key=key))
        if note:
            ui.say(note)
        if not projects:
            ui.say(
                "No accessible projects found yet. Check the key's project access in the settings page above."
            )
        action = ui.choose(
            "Which project should OpenSRE use?",
            [
                *projects,
                ("retry", "Retry project discovery"),
                ("key", "Paste another personal key"),
                ("host", "Change region / app address"),
                ("manual", "My project isn't listed — enter its ID"),
                ("cancel", "Cancel"),
            ],
        )
        if action == "retry":
            continue
        if action == "key":
            key = ui.value("Paste your personal API key (hidden)", secret=True)
            continue
        if action == "host":
            host = _host(ui, {})
            ui.say(f"Open {host}/settings/user-api-keys and create a key for this account.")
            key = ui.value("Paste this account's personal API key (hidden)", secret=True)
            continue
        if action == "manual":
            ui.say(f"Open {host}/settings/project and copy the Project ID shown there.")
            project_id = ui.value("Project ID")
        else:
            project_id = action
        ui.say(f"Selected project {project_id} on {host}.")
        return {"base_url": host, "personal_api_key": key, "project_id": project_id}
