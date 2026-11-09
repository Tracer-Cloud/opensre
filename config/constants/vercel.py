"""Vercel environment variable names and shared static constants.

``VERCEL_API_BASE_URL`` is the account/team management API — projects,
deployments, logs, and (for ``infrastructure.filestorage.providers.vercel``) Blob
store metadata. It is a *different* host and audience than the Blob
data-plane API (``https://vercel.com/api/blob``, authenticated with
``BLOB_READ_WRITE_TOKEN``), which stays local to
``infrastructure.filestorage.providers.vercel`` since nothing else calls it.
"""

from __future__ import annotations

VERCEL_API_TOKEN_ENV = "VERCEL_API_TOKEN"
VERCEL_TEAM_ID_ENV = "VERCEL_TEAM_ID"
VERCEL_API_BASE_URL = "https://api.vercel.com"
VERCEL_RUNTIME_LOGS_READ_TIMEOUT_ENV = "VERCEL_RUNTIME_LOGS_READ_TIMEOUT"

__all__ = [
    "VERCEL_API_BASE_URL",
    "VERCEL_API_TOKEN_ENV",
    "VERCEL_RUNTIME_LOGS_READ_TIMEOUT_ENV",
    "VERCEL_TEAM_ID_ENV",
]
