"""What the OpenSRE Analytics connection needs before it is considered configured."""

from __future__ import annotations

from config.constants.opensre_analytics import (
    OPENSRE_ANALYTICS_API_TOKEN_ENV,
    OPENSRE_ANALYTICS_URL_ENV,
)
from integrations.opensre_analytics.config import DEFAULT_OPENSRE_ANALYTICS_URL, SERVICE
from integrations.opensre_analytics.verifier import verify_opensre_analytics
from integrations.setup_flow import IntegrationSetupSpec, SetupField

URL_FIELD = "url"
AUTH_TOKEN_FIELD = "auth_token"

OPENSRE_ANALYTICS_SETUP = IntegrationSetupSpec(
    service=SERVICE,
    fields=(
        SetupField(
            name=URL_FIELD,
            label="OpenSRE Analytics URL",
            env_var=OPENSRE_ANALYTICS_URL_ENV,
            default=DEFAULT_OPENSRE_ANALYTICS_URL,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="API token",
            prompt="API token (the analytics app's ANALYTICS_API_TOKEN)",
            env_var=OPENSRE_ANALYTICS_API_TOKEN_ENV,
            secret=True,
        ),
    ),
    verify=verify_opensre_analytics,
)

__all__ = [
    "AUTH_TOKEN_FIELD",
    "OPENSRE_ANALYTICS_SETUP",
    "URL_FIELD",
]
