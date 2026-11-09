"""Configurator handler for the GitLab integration."""

from __future__ import annotations

from integrations.gitlab import GITLAB_SETUP
from surfaces.cli.wizard.configurators.spec_configurator import configure_from_spec


def _configure_gitlab() -> tuple[str, str]:
    return configure_from_spec(GITLAB_SETUP, title="GitLab")
