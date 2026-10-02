"""Where a chat user connects an integration: the OpenSRE web app, not a terminal."""

from __future__ import annotations

from config.account import webapp_base_url
from config.constants.account import OPENSRE_GITHUB_SETTINGS_PATH

_INTEGRATIONS_PATH = "/integrations"


def web_setup_url(service: str) -> str:
    """The web app page that connects ``service`` for the workspace, or "" when unknown.

    GitHub has its own settings page (connect, reconnect, update permissions);
    every other service uses its Integrations page.
    """
    base = webapp_base_url()
    name = service.strip().lower()
    if not base or not name:
        return ""
    if name == "github":
        return f"{base}{OPENSRE_GITHUB_SETTINGS_PATH}"
    return f"{base}{_INTEGRATIONS_PATH}/{name.replace('_', '-')}"


__all__ = ["web_setup_url"]
