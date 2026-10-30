"""Dynatrace setup specification."""

from __future__ import annotations

from config.constants.dynatrace import DYNATRACE_PLATFORM_TOKEN_ENV, DYNATRACE_TENANT_URL_ENV
from integrations.dynatrace.verifier import verify_dynatrace
from integrations.setup_flow import IntegrationSetupSpec, SetupField

TENANT_URL_FIELD = "tenant_url"
PLATFORM_TOKEN_FIELD = "platform_token"

DYNATRACE_SETUP = IntegrationSetupSpec(
    service="dynatrace",
    fields=(
        SetupField(
            name=TENANT_URL_FIELD,
            label="Dynatrace tenant URL",
            prompt="Tenant URL (for example, https://abc123.apps.dynatrace.com)",
            env_var=DYNATRACE_TENANT_URL_ENV,
        ),
        SetupField(
            name=PLATFORM_TOKEN_FIELD,
            label="Dynatrace platform token",
            prompt="Platform token with storage:buckets:read and storage:logs:read",
            env_var=DYNATRACE_PLATFORM_TOKEN_ENV,
            secret=True,
        ),
    ),
    verify=verify_dynatrace,
)

__all__ = ["DYNATRACE_SETUP", "PLATFORM_TOKEN_FIELD", "TENANT_URL_FIELD"]
