"""What GitLab needs before it is considered configured.

``base_url`` defaults to gitlab.com's API root and moves only for self-managed
instances. The token is required — the previous CLI handler accepted a blank one
and stored an integration that could not authenticate.
"""

from __future__ import annotations

from config.constants.gitlab import GITLAB_AUTH_TOKEN_ENV, GITLAB_BASE_URL_ENV
from integrations.gitlab.client import DEFAULT_GITLAB_BASE_URL
from integrations.gitlab.verifier import verify_gitlab
from integrations.setup_flow import IntegrationSetupSpec, SetupField

BASE_URL_FIELD = "base_url"
AUTH_TOKEN_FIELD = "auth_token"

GITLAB_SETUP = IntegrationSetupSpec(
    service="gitlab",
    fields=(
        SetupField(
            name=BASE_URL_FIELD,
            label="GitLab base URL",
            prompt="GitLab base URL (e.g. https://gitlab.example.com/api/v4)",
            env_var=GITLAB_BASE_URL_ENV,
            default=DEFAULT_GITLAB_BASE_URL,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="GitLab access token",
            env_var=GITLAB_AUTH_TOKEN_ENV,
            secret=True,
        ),
    ),
    verify=verify_gitlab,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "BASE_URL_FIELD",
    "GITLAB_SETUP",
]
