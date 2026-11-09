"""What Azure Monitor Log Analytics needs before it is considered configured.

Workspace id and bearer token are both required: the query API is scoped to a
workspace and refuses an empty token rather than falling back to another auth
path. Endpoint, tenant, subscription, and the row cap are optional — sovereign
clouds override the public host; tenant/subscription are informational.
"""

from __future__ import annotations

from config.constants.azure import (
    AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT,
    AZURE_LOG_ANALYTICS_ENDPOINT_ENV,
    AZURE_LOG_ANALYTICS_TOKEN_ENV,
    AZURE_LOG_ANALYTICS_WORKSPACE_ID_ENV,
    AZURE_MAX_RESULTS_DEFAULT,
    AZURE_MAX_RESULTS_ENV,
    AZURE_SUBSCRIPTION_ID_ENV,
    AZURE_TENANT_ID_ENV,
)
from integrations.azure.verifier import verify_azure
from integrations.setup_flow import IntegrationSetupSpec, SetupField

WORKSPACE_ID_FIELD = "workspace_id"
ACCESS_TOKEN_FIELD = "access_token"
ENDPOINT_FIELD = "endpoint"
TENANT_ID_FIELD = "tenant_id"
SUBSCRIPTION_ID_FIELD = "subscription_id"
MAX_RESULTS_FIELD = "max_results"

AZURE_SETUP = IntegrationSetupSpec(
    service="azure",
    fields=(
        SetupField(
            name=WORKSPACE_ID_FIELD,
            label="Log Analytics workspace ID",
            prompt="Workspace ID (GUID from the Azure portal)",
            env_var=AZURE_LOG_ANALYTICS_WORKSPACE_ID_ENV,
        ),
        SetupField(
            name=ACCESS_TOKEN_FIELD,
            label="Azure AD bearer token",
            env_var=AZURE_LOG_ANALYTICS_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name=ENDPOINT_FIELD,
            label="Log Analytics endpoint",
            prompt="Endpoint (sovereign-cloud host, or leave default)",
            env_var=AZURE_LOG_ANALYTICS_ENDPOINT_ENV,
            default=AZURE_LOG_ANALYTICS_DEFAULT_ENDPOINT,
            required=False,
        ),
        SetupField(
            name=TENANT_ID_FIELD,
            label="Microsoft Entra tenant ID",
            prompt="Tenant ID (optional, informational)",
            env_var=AZURE_TENANT_ID_ENV,
            required=False,
        ),
        SetupField(
            name=SUBSCRIPTION_ID_FIELD,
            label="Azure subscription ID",
            prompt="Subscription ID (optional, informational)",
            env_var=AZURE_SUBSCRIPTION_ID_ENV,
            required=False,
        ),
        SetupField(
            name=MAX_RESULTS_FIELD,
            label="Per-query row cap",
            prompt="Max results per query (capped at 200)",
            env_var=AZURE_MAX_RESULTS_ENV,
            default=str(AZURE_MAX_RESULTS_DEFAULT),
            required=False,
        ),
    ),
    verify=verify_azure,
)

__all__ = [
    "ACCESS_TOKEN_FIELD",
    "AZURE_SETUP",
    "ENDPOINT_FIELD",
    "MAX_RESULTS_FIELD",
    "SUBSCRIPTION_ID_FIELD",
    "TENANT_ID_FIELD",
    "WORKSPACE_ID_FIELD",
]
