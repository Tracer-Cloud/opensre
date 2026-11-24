"""Hydrate the local integration store from a Secrets Manager v2 payload.

Peer of :mod:`integrations.credentials_api`: the transport differs — a silo's
task role reads one pinned secret ARN instead of calling the webapp over
HTTPS — but the payload contract (IntegrationStore v2) and the resulting local
store are identical. The webapp owns the secret's contents; it writes them
through the control plane, which rolls the silo so this runs again at boot.

Errors carry a generic message: the caller logs them, and a validation failure
must never echo a credential back into the log stream.
"""

from __future__ import annotations

import json
from typing import Any

from config.constants.organization import organization_id
from integrations.credentials_api import (
    CredentialsApiError,
    IntegrationStoreV2,
    validate_integration_store_v2,
)
from integrations.store import replace_integrations


class IntegrationsSecretError(RuntimeError):
    """Raised with a generic message when the integrations secret is unusable."""


def parse_integrations_secret(secret_string: str) -> IntegrationStoreV2:
    """Validate one Secrets Manager payload without touching the local store."""
    if not secret_string.strip():
        raise IntegrationsSecretError("Integrations secret is empty")
    try:
        payload = json.loads(secret_string)
    except ValueError:
        raise IntegrationsSecretError("Integrations secret is not valid JSON") from None
    try:
        return validate_integration_store_v2(payload)
    except CredentialsApiError:
        raise IntegrationsSecretError("Integrations secret is not a valid credential set") from None


def hydrate_integration_store_from_secret(secret_string: str) -> IntegrationStoreV2:
    """Validate the secret and atomically materialize the local v2 store."""
    # The store this fills is shared by every member the host serves, so a
    # member's personal grant never lands in it, whatever the secret holds.
    validated = parse_integrations_secret(secret_string).visible_to(
        user_id=None, organization_id=organization_id() or None
    )
    integrations: Any = validated.as_store_data()["integrations"]
    if not isinstance(integrations, list):
        raise IntegrationsSecretError("Integrations secret is not a valid credential set")
    replace_integrations(integrations)
    return validated


__all__ = [
    "IntegrationsSecretError",
    "hydrate_integration_store_from_secret",
    "parse_integrations_secret",
]
