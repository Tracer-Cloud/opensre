"""Config-owned storage helpers for LLM provider auth metadata.

Credential operations are imported from :mod:`config.llm_auth.credentials`
directly, never re-exported here: this init must stay free of that module so
leaf importers (``provider_catalog``) never pull in :mod:`config.secrets.store`.
"""

from __future__ import annotations

from config.llm_auth.provider_catalog import (
    API_KEY_PROVIDER_ENVS,
    KEYLESS_PROVIDER_VALUES,
    PROVIDER_SPECS,
    SUPPORTED_PROVIDER_VALUES,
    ProviderSpec,
    provider_spec,
)
from config.llm_auth.records import (
    delete_provider_auth_record,
    provider_auth_record_name,
    resolve_provider_auth_record,
    save_provider_auth_record,
)

__all__ = [
    "API_KEY_PROVIDER_ENVS",
    "KEYLESS_PROVIDER_VALUES",
    "PROVIDER_SPECS",
    "ProviderSpec",
    "SUPPORTED_PROVIDER_VALUES",
    "delete_provider_auth_record",
    "provider_auth_record_name",
    "provider_spec",
    "resolve_provider_auth_record",
    "save_provider_auth_record",
]
