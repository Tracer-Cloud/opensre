"""What PostHog needs before it is considered configured.

The project ID is required alongside the key: every query endpoint is addressed
per project, so a key on its own cannot reach any data.

``base_url`` defaults to PostHog's US cloud and moves for EU or self-hosted.
"""

from __future__ import annotations

from config.constants.posthog import (
    DEFAULT_POSTHOG_URL,
    POSTHOG_BASE_URL_ENV,
    POSTHOG_PERSONAL_API_KEY_ENV,
    POSTHOG_PROJECT_ID_ENV,
)
from integrations.posthog.setup_guide import guide_posthog
from integrations.posthog.verifier import verify_posthog
from integrations.setup_flow import IntegrationSetupSpec, SetupField

BASE_URL_FIELD = "base_url"
PROJECT_ID_FIELD = "project_id"
PERSONAL_API_KEY_FIELD = "personal_api_key"

POSTHOG_SETUP = IntegrationSetupSpec(
    service="posthog",
    guide=guide_posthog,
    fields=(
        SetupField(
            name=BASE_URL_FIELD,
            label="PostHog API base URL",
            env_var=POSTHOG_BASE_URL_ENV,
            default=DEFAULT_POSTHOG_URL,
        ),
        SetupField(
            name=PROJECT_ID_FIELD,
            label="PostHog project ID",
            env_var=POSTHOG_PROJECT_ID_ENV,
        ),
        SetupField(
            name=PERSONAL_API_KEY_FIELD,
            label="PostHog personal API key",
            prompt="PostHog personal API key (phx_...)",
            env_var=POSTHOG_PERSONAL_API_KEY_ENV,
            secret=True,
        ),
    ),
    verify=verify_posthog,
)

__all__ = [
    "BASE_URL_FIELD",
    "PERSONAL_API_KEY_FIELD",
    "POSTHOG_SETUP",
    "PROJECT_ID_FIELD",
]
