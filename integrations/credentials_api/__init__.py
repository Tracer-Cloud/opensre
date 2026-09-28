"""Credentials API client and validated integration-store contracts."""

from integrations.credentials_api.client import (
    CredentialsApiClient,
    CredentialsApiError,
    IntegrationInstanceV2,
    IntegrationRecordV2,
    IntegrationStoreV2,
    hydrate_integration_store,
    materialize_integration_store,
    validate_integration_store_v2,
)

__all__ = [
    "CredentialsApiClient",
    "CredentialsApiError",
    "IntegrationInstanceV2",
    "IntegrationRecordV2",
    "IntegrationStoreV2",
    "hydrate_integration_store",
    "materialize_integration_store",
    "validate_integration_store_v2",
]
