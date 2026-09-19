"""What Observe needs before it is considered configured."""

from __future__ import annotations

from config.constants.observe import (
    OBSERVE_API_TOKEN_ENV,
    OBSERVE_BASE_URL_ENV,
    OBSERVE_CUSTOMER_ID_ENV,
)
from integrations.observe.verifier import verify_observe
from integrations.setup_flow import IntegrationSetupSpec, SetupField

OBSERVE_SETUP = IntegrationSetupSpec(
    service="observe",
    fields=(
        SetupField(
            name="customer_id",
            label="Customer ID",
            prompt="Customer ID (numeric, e.g. 168955706525)",
            env_var=OBSERVE_CUSTOMER_ID_ENV,
        ),
        SetupField(
            name="api_token",
            label="API token",
            env_var=OBSERVE_API_TOKEN_ENV,
            secret=True,
        ),
        SetupField(
            name="base_url",
            label="Base URL",
            prompt="Base URL (leave blank to derive from customer ID)",
            env_var=OBSERVE_BASE_URL_ENV,
            required=False,
        ),
    ),
    verify=verify_observe,
)

__all__ = ["OBSERVE_SETUP"]
