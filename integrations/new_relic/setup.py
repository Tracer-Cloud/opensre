"""What New Relic needs before it is considered configured.

Both ``api_key`` and ``account_id`` are required (FR-2) — NerdGraph queries
always target a specific account, so a key without an account id (or vice
versa) configures nothing usable. ``base_url`` moves only for EU/JP tenants.
"""

from __future__ import annotations

from config.constants.new_relic import (
    NEW_RELIC_ACCOUNT_ID_ENV,
    NEW_RELIC_API_KEY_ENV,
    NEW_RELIC_BASE_URL_ENV,
)
from integrations.new_relic.config import DEFAULT_NEW_RELIC_BASE_URL
from integrations.new_relic.verifier import verify_new_relic
from integrations.setup_flow import IntegrationSetupSpec, SetupField

API_KEY_FIELD = "api_key"
ACCOUNT_ID_FIELD = "account_id"
BASE_URL_FIELD = "base_url"

NEW_RELIC_SETUP = IntegrationSetupSpec(
    service="new_relic",
    fields=(
        SetupField(
            name=API_KEY_FIELD,
            label="New Relic API key",
            prompt="User key (NRAK-...)",
            env_var=NEW_RELIC_API_KEY_ENV,
            secret=True,
        ),
        SetupField(
            name=ACCOUNT_ID_FIELD,
            label="New Relic account ID",
            prompt="Account ID",
            env_var=NEW_RELIC_ACCOUNT_ID_ENV,
        ),
        SetupField(
            name=BASE_URL_FIELD,
            label="New Relic API URL",
            prompt="API URL (US default, or the EU/JP endpoint)",
            env_var=NEW_RELIC_BASE_URL_ENV,
            default=DEFAULT_NEW_RELIC_BASE_URL,
        ),
    ),
    verify=verify_new_relic,
)

__all__ = [
    "ACCOUNT_ID_FIELD",
    "API_KEY_FIELD",
    "BASE_URL_FIELD",
    "NEW_RELIC_SETUP",
]
