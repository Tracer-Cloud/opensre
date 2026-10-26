"""Persist remote-sync settings (shared by CLI and slash ``setup``).

Writes the ``remote_sync`` section of ``~/.opensre/config.yml``. Ambient cloud
credentials (AWS profile session, GCP Application Default Credentials,
``BLOB_READ_WRITE_TOKEN``, …) stay outside this file — opensre never stores
them. ``update_section`` merges, so keys written by older versions survive;
setup itself supplies exactly the six values below and never anything
credential-shaped.
"""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
)
from config.local_settings import LocalSettingsError, update_section
from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.enums import RemoteSyncField
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.providers.registry import (
    provider_extra_fields,
    registered_providers,
)


@dataclass(frozen=True)
class RemoteSyncSetupRequest:
    """Values to store for the next laptop that loads config from disk."""

    bucket: str
    provider: str = DEFAULT_REMOTE_SYNC_PROVIDER
    prefix: str = DEFAULT_REMOTE_SYNC_PREFIX
    region: str = ""
    profile: str = ""
    enabled: bool = True


class RemoteSyncSetupError(RemoteSyncConfigError):
    """Side-effect-free setup validation failure safe to show on every surface."""

    def __init__(self, message: str, *, field: str) -> None:
        super().__init__(message)
        self.field = field


def validate_remote_sync_settings(request: RemoteSyncSetupRequest) -> RemoteSyncSetupRequest:
    """Normalize setup values without reading credentials or writing settings."""
    bucket = request.bucket.strip()
    if not bucket:
        raise RemoteSyncSetupError("bucket (store name) is required", field="bucket")
    provider = request.provider.strip().lower() or DEFAULT_REMOTE_SYNC_PROVIDER
    known = registered_providers()
    if provider not in known:
        listed = ", ".join(known) or "(none)"
        raise RemoteSyncSetupError(
            f"unknown remote-sync provider {provider!r}; known: {listed}", field="provider"
        )
    prefix = request.prefix.strip() or DEFAULT_REMOTE_SYNC_PREFIX
    region = request.region.strip()
    profile = request.profile.strip()
    declared = {extra.field for extra in provider_extra_fields(provider)}
    for field, value in ((RemoteSyncField.REGION, region), (RemoteSyncField.PROFILE, profile)):
        if value and field not in declared:
            raise RemoteSyncSetupError(
                f"--{field.value} is not used by provider {provider!r}; "
                "drop the flag or switch providers",
                field=field.value,
            )
    return RemoteSyncSetupRequest(
        bucket=bucket,
        provider=provider,
        prefix=prefix,
        region=region,
        profile=profile,
        enabled=request.enabled,
    )


def save_remote_sync_settings(request: RemoteSyncSetupRequest) -> RemoteSyncConfig:
    """Merge validated settings into ``remote_sync``; environment overrides still win."""
    request = validate_remote_sync_settings(request)
    bucket, provider, prefix = request.bucket, request.provider, request.prefix
    region, profile = request.region, request.profile
    try:
        update_section(
            "remote_sync",
            {
                "enabled": bool(request.enabled),
                "provider": provider,
                "bucket": bucket,
                "prefix": prefix,
                "region": region,
                "profile": profile,
            },
        )
    except LocalSettingsError as exc:
        raise RemoteSyncConfigError(str(exc)) from exc
    return RemoteSyncConfig(
        bucket=bucket,
        provider=provider,
        prefix=prefix,
        region=region,
        profile=profile,
    )


def disable_remote_sync() -> None:
    """Switch stored sync off, keeping the rest of the section for later.

    Pure merge: the stored provider/bucket/prefix survive, so re-enabling is a
    one-line change rather than a full setup again.
    """
    try:
        update_section("remote_sync", {"enabled": False})
    except LocalSettingsError as exc:
        raise RemoteSyncConfigError(str(exc)) from exc


__all__ = [
    "RemoteSyncSetupError",
    "RemoteSyncSetupRequest",
    "disable_remote_sync",
    "save_remote_sync_settings",
    "validate_remote_sync_settings",
]
