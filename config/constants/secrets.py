"""Env-var names and static identifiers for local secret storage."""

from __future__ import annotations

from typing import Final

# Skip local credential storage entirely. Fail-closed: nothing is written to
# disk, so credentials must come from the process environment. The name
# predates the removal of the OS keyring tier and is kept for compatibility
# with existing setups (CI exports it).
OPENSRE_DISABLE_KEYRING_ENV: Final = "OPENSRE_DISABLE_KEYRING"

CREDENTIAL_FALLBACK_FILENAME: Final = "credentials.json"

__all__ = [
    "CREDENTIAL_FALLBACK_FILENAME",
    "OPENSRE_DISABLE_KEYRING_ENV",
]
