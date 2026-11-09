"""Env names for optional remote context sync to a user-owned object store.

Opt-in and off by default. A laptop keeps working entirely on local disk; when
enabled, conversation history and memory are mirrored to a store the user owns
so a second machine can pick up where the first left off.

The backend is selected by ``OPENSRE_REMOTE_SYNC_PROVIDER`` (default ``aws``;
built-in also ``gcs`` and ``vercel``). New vendors register under
``infrastructure.filestorage.providers`` without changing the sync engine.
"""

from __future__ import annotations

# Master switch. Sync stays off until this is truthy, even if a bucket is named.
REMOTE_SYNC_ENV = "OPENSRE_REMOTE_SYNC"
# Cloud provider registered in infrastructure.filestorage.providers (default: aws).
# Value must stay aligned with BuiltInProvider in infrastructure.filestorage.enums.
REMOTE_SYNC_PROVIDER_ENV = "OPENSRE_REMOTE_SYNC_PROVIDER"
# Top-level store name the user owns (S3 bucket, GCS bucket, Vercel Blob store
# name/id, …). Required when sync is on.
REMOTE_SYNC_BUCKET_ENV = "OPENSRE_REMOTE_SYNC_BUCKET"
# Key prefix inside the store, so one store can hold several roots.
REMOTE_SYNC_PREFIX_ENV = "OPENSRE_REMOTE_SYNC_PREFIX"
# Region override; falls back to the ambient cloud configuration when supported.
REMOTE_SYNC_REGION_ENV = "OPENSRE_REMOTE_SYNC_REGION"
# Named credentials profile, for users who keep opensre credentials separate.
REMOTE_SYNC_PROFILE_ENV = "OPENSRE_REMOTE_SYNC_PROFILE"
# Comma-separated glob patterns held back from the sync. Subtractive only: a
# pattern can shrink what mirrors, never widen it past the credential deny-list.
REMOTE_SYNC_EXCLUDE_ENV = "OPENSRE_REMOTE_SYNC_EXCLUDE"
# Turns the configured exclusions off for one run. A separate switch rather than
# a reserved pattern value: every value the pattern field accepts is also a legal
# glob, so a sentinel would steal that filename from anyone wanting to exclude it.
REMOTE_SYNC_EXCLUDE_OFF_ENV = "OPENSRE_REMOTE_SYNC_EXCLUDE_OFF"
# Vercel Blob read-write token (ambient; never stored by opensre).
# Same name Vercel documents for @vercel/blob / vercel CLI.
BLOB_READ_WRITE_TOKEN_ENV = "BLOB_READ_WRITE_TOKEN"

# Endpoint URL override for S3-compatible stores (MinIO, R2, Spaces, …).
REMOTE_SYNC_ENDPOINT_URL_ENV = "OPENSRE_REMOTE_SYNC_ENDPOINT_URL"

DEFAULT_REMOTE_SYNC_PREFIX = "opensre"
DEFAULT_REMOTE_SYNC_PROVIDER = "aws"
# Uploads run in parallel, capped per provider. This is the cap for a provider
# that declares none: deliberately low, because an undeclared limit means an
# unknown one, and a throttled write aborts the whole push. Providers that know
# they tolerate more say so via ``register_object_store``.
DEFAULT_MAX_PARALLEL_UPLOADS = 4

__all__ = [
    "BLOB_READ_WRITE_TOKEN_ENV",
    "DEFAULT_MAX_PARALLEL_UPLOADS",
    "DEFAULT_REMOTE_SYNC_PREFIX",
    "DEFAULT_REMOTE_SYNC_PROVIDER",
    "REMOTE_SYNC_BUCKET_ENV",
    "REMOTE_SYNC_ENDPOINT_URL_ENV",
    "REMOTE_SYNC_ENV",
    "REMOTE_SYNC_EXCLUDE_ENV",
    "REMOTE_SYNC_EXCLUDE_OFF_ENV",
    "REMOTE_SYNC_PREFIX_ENV",
    "REMOTE_SYNC_PROFILE_ENV",
    "REMOTE_SYNC_PROVIDER_ENV",
    "REMOTE_SYNC_REGION_ENV",
]
