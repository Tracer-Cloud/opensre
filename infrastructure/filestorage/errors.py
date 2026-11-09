"""Errors raised while mirroring context to a user-owned bucket."""

from __future__ import annotations


class RemoteSyncError(RuntimeError):
    """Base for every remote-sync failure."""


class RemoteSyncConfigError(RemoteSyncError):
    """Sync is switched on but the settings are unusable."""


class RemoteSyncUnavailableError(RemoteSyncError):
    """The bucket could not be reached or the credentials were rejected."""


class OrgScopeNotSupportedError(RemoteSyncError):
    """Raised when an organization-scoped turn asks for remote sync.

    Bucket keys are not namespaced by principal or actor, so two members of one
    organization would share every key and read each other's conversations.
    Organization data already persists through the mounted context root, so this
    fails closed rather than mirroring anything.
    """


class UnsyncablePathError(RemoteSyncError):
    """A path outside the syncable roots was offered for upload.

    Raised rather than skipped: reaching this means a caller tried to mirror
    something the user did not agree to share, and silence would hide it.
    """


__all__ = [
    "OrgScopeNotSupportedError",
    "RemoteSyncConfigError",
    "RemoteSyncError",
    "RemoteSyncUnavailableError",
    "UnsyncablePathError",
]
