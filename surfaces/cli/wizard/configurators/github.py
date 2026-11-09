"""Onboarding handoff to GitHub setup in the OpenSRE app."""

from integrations.github import setup_github


def _configure_github_app() -> tuple[str, str]:
    """Open app setup without reporting a connected integration."""
    setup_github()
    return "", ""


__all__ = ["_configure_github_app"]
