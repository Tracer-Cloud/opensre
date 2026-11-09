"""What Groundcover needs before it is considered configured.

Only the service-account key is required. The tenant and backend identifiers
narrow which workspace and backend queries address, and are needed only by
multi-workspace or multi-backend accounts.

The key is written as ``GROUNDCOVER_API_KEY``; credential resolution also
accepts ``GROUNDCOVER_MCP_TOKEN`` as a fallback, but setup writes the primary
name so there is one place to look.
"""

from __future__ import annotations

from config.constants.groundcover import (
    GROUNDCOVER_API_KEY_ENV,
    GROUNDCOVER_BACKEND_ID_ENV,
    GROUNDCOVER_MCP_URL_ENV,
    GROUNDCOVER_TENANT_UUID_ENV,
    GROUNDCOVER_TIMEZONE_ENV,
)
from integrations.groundcover.config import (
    DEFAULT_GROUNDCOVER_MCP_URL,
    DEFAULT_GROUNDCOVER_TIMEZONE,
)
from integrations.groundcover.verifier import verify_groundcover
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_KEY_FIELD = "api_key"
MCP_URL_FIELD = "mcp_url"
TENANT_UUID_FIELD = "tenant_uuid"
BACKEND_ID_FIELD = "backend_id"
TIMEZONE_FIELD = "timezone"

GROUNDCOVER_SETUP = IntegrationSetupSpec(
    service="groundcover",
    fields=(
        SetupField(
            name=API_KEY_FIELD,
            label="Groundcover API key",
            prompt="Service-account API key",
            env_var=GROUNDCOVER_API_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=MCP_URL_FIELD,
            label="Groundcover MCP URL",
            prompt="MCP URL",
            env_var=GROUNDCOVER_MCP_URL_ENV,
            default=DEFAULT_GROUNDCOVER_MCP_URL,
        ),
        SetupField(
            name=TENANT_UUID_FIELD,
            label="Groundcover tenant UUID",
            prompt="Tenant UUID (optional, for multi-workspace accounts)",
            env_var=GROUNDCOVER_TENANT_UUID_ENV,
            required=False,
        ),
        SetupField(
            name=BACKEND_ID_FIELD,
            label="Groundcover backend ID",
            prompt="Backend ID (optional, for multi-backend tenants)",
            env_var=GROUNDCOVER_BACKEND_ID_ENV,
            required=False,
        ),
        SetupField(
            name=TIMEZONE_FIELD,
            label="Groundcover timezone",
            prompt="Timezone",
            env_var=GROUNDCOVER_TIMEZONE_ENV,
            default=DEFAULT_GROUNDCOVER_TIMEZONE,
        ),
    ),
    verify=verify_groundcover,
)

__all__ = [
    "API_KEY_FIELD",
    "BACKEND_ID_FIELD",
    "GROUNDCOVER_SETUP",
    "MCP_URL_FIELD",
    "TENANT_UUID_FIELD",
    "TIMEZONE_FIELD",
]
