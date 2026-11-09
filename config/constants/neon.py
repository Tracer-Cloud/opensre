"""Neon environment variable names and shared static constants.

``NEON_API_BASE_URL`` is the Neon control-plane API (projects, branches,
computes, operations, and Object Storage buckets). Postgres connections and
the S3-compatible Object Storage data plane use per-branch hosts that the API
reports, so neither has a fixed constant here.
"""

from __future__ import annotations

NEON_API_KEY_ENV = "NEON_API_KEY"
NEON_PROJECT_ID_ENV = "NEON_PROJECT_ID"
NEON_BRANCH_ID_ENV = "NEON_BRANCH_ID"
NEON_API_BASE_URL = "https://console.neon.tech/api/v2"

__all__ = [
    "NEON_API_BASE_URL",
    "NEON_API_KEY_ENV",
    "NEON_BRANCH_ID_ENV",
    "NEON_PROJECT_ID_ENV",
]
