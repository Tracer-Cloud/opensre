"""Settings for optional remote context sync.

Environment variables take precedence over ``~/.opensre/config.yml`` section
``remote_sync``, so a one-off export can redirect a single run without editing
the file. Naming a bucket alone never enables sync — the switch must be on in
env or in the stored section. An unreadable stored section behaves like no
stored settings whenever the environment supplies the switch and the bucket;
it only fails the run when a value has no other source (the switch, or the
bucket).
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from config.constants.filestorage import (
    DEFAULT_REMOTE_SYNC_PREFIX,
    DEFAULT_REMOTE_SYNC_PROVIDER,
    REMOTE_SYNC_BUCKET_ENV,
    REMOTE_SYNC_ENV,
    REMOTE_SYNC_EXCLUDE_ENV,
    REMOTE_SYNC_EXCLUDE_OFF_ENV,
    REMOTE_SYNC_PREFIX_ENV,
    REMOTE_SYNC_PROFILE_ENV,
    REMOTE_SYNC_PROVIDER_ENV,
    REMOTE_SYNC_REGION_ENV,
)
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.exclusions import NO_EXCLUSIONS, ExclusionRules, parse_exclusions

_TRUTHY = frozenset({"1", "true", "yes", "on"})


@dataclass(frozen=True)
class RemoteSyncConfig:
    """Where a laptop mirrors its context, under which backend and credentials.

    ``bucket`` is the top-level store name for the chosen provider (S3 bucket,
    GCS bucket, or Vercel Blob store name/id; community backends may reuse the
    field). Provider-specific fields (``profile``, ``region``) are ignored by
    backends that do not need them.

    ``exclude`` narrows what mirrors. It cannot widen it: the credential
    deny-list is enforced separately, in
    :mod:`infrastructure.filestorage.syncable`.
    """

    bucket: str
    provider: str = DEFAULT_REMOTE_SYNC_PROVIDER
    prefix: str = DEFAULT_REMOTE_SYNC_PREFIX
    region: str = ""
    profile: str = ""
    exclude: ExclusionRules = NO_EXCLUSIONS

    def key_for(self, relative_key: str) -> str:
        """Full object key for a path relative to the synced root."""
        return f"{self.prefix.rstrip('/')}/{relative_key.lstrip('/')}"


def _stored_section() -> dict[str, Any] | None:
    """Stored settings, or ``None`` when the file cannot be read.

    A damaged ``config.yml`` counts as "no stored settings": when the
    environment already names the switch and the bucket, the documented
    env-only setup must not be blocked by an unrelated damaged section.
    Settings that can only come from the file fail separately, in
    :func:`_required_stored`.
    """
    from config.local_settings import LocalSettingsError, read_section

    try:
        return read_section("remote_sync")
    except LocalSettingsError:
        return None


def _required_stored(stored: Callable[[], dict[str, Any] | None]) -> dict[str, Any]:
    """The stored section, raising when the file cannot be read.

    Only for values the environment cannot supply: ``enabled`` when the switch
    env var is unset, and ``bucket`` when the bucket env var is unset.
    """
    from config.local_settings import local_settings_path

    section = stored()
    if section is None:
        raise RemoteSyncConfigError(
            f"remote_sync settings cannot be read from {local_settings_path()}"
        )
    return section


def _truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in _TRUTHY
    return False


def _validated_scalar(value: Any, key: str) -> Any:
    """Reject a stored ``remote_sync`` value that cannot be a single setting.

    A YAML sequence or mapping under a scalar key (``bucket: [my-bucket]``)
    would otherwise stringify to something like ``"['my-bucket']"`` and fail
    much later against the storage backend. Caught here, at read time, so the
    error names the key that is actually wrong.
    """
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise RemoteSyncConfigError(
        f"remote_sync.{key} must be a single value, not a {type(value).__name__}"
    )


def _env_or_stored(
    env_name: str,
    stored_key: str,
    stored: Callable[[], dict[str, Any] | None],
) -> str:
    """Environment value, else the stored one. The file is read only if needed.

    Validated only on the fallback path: a malformed stored value must not
    break a field that the environment already overrides. An unreadable
    stored section leaves the field empty, so the caller's default applies.
    """
    env = os.getenv(env_name, "").strip()
    if env:
        return env
    section = stored()
    if section is None:
        return ""
    value = _validated_scalar(section.get(stored_key), stored_key)
    if value is None:
        return ""
    return str(value).strip()


def stored_remote_sync_value(key: str) -> str:
    """A single scalar from the stored ``remote_sync`` section, or ``""``.

    For a provider-specific setting outside ``RemoteSyncConfig``'s two
    extension slots (``region``/``profile``) that still deserves
    ``config.yml`` persistence the same way ``bucket``/``region``/``profile``
    get it — e.g. s3compat's endpoint URL. Reads the file fresh on every call
    (no caching), so callers should already have checked the environment
    first and only fall back to this on a miss. An unreadable stored section
    mirrors "no stored settings" here too, same as :func:`_env_or_stored`.
    """
    section = _stored_section()
    if section is None:
        return ""
    value = _validated_scalar(section.get(key), key)
    if value is None:
        return ""
    return str(value).strip()


def _exclusions(stored: Callable[[], dict[str, Any] | None]) -> ExclusionRules:
    """Patterns from the environment, else the stored list.

    Kept apart from :func:`_env_or_stored` because the stored form may be a
    YAML list while the environment can only ever be one string, and a list
    must not be flattened to ``"['a', 'b']"`` on the way through.

    An empty variable means "not set", as it does for every other setting here,
    so the stored patterns still apply. Reading it as "exclude nothing" would
    make ``export OPENSRE_REMOTE_SYNC_EXCLUDE=$UNSET`` silently upload the paths
    the user asked to keep local. Turning the list off for a run is its own
    switch, which has to be set on purpose and cannot be produced by a blank.

    An unreadable stored section mirrors "no stored settings", so the built-in
    deny-list in :mod:`infrastructure.filestorage.syncable` remains the boundary
    that keeps credentials local.
    """
    if _truthy(os.getenv(REMOTE_SYNC_EXCLUDE_OFF_ENV, "")):
        return NO_EXCLUSIONS
    env = os.getenv(REMOTE_SYNC_EXCLUDE_ENV, "").strip()
    if env:
        return parse_exclusions(env)
    section = stored()
    if section is None:
        return NO_EXCLUSIONS
    return parse_exclusions(section.get("exclude"))


def _lazy_stored() -> Callable[[], dict[str, Any] | None]:
    """Read the settings file at most once, and only if something needs it.

    A run configured entirely through the environment must not fail because an
    unrelated section of ``config.yml`` is damaged.
    """
    cache: dict[str, dict[str, Any] | None] = {}

    def read() -> dict[str, Any] | None:
        if "section" not in cache:
            cache["section"] = _stored_section()
        return cache["section"]

    return read


def _enabled(stored: Callable[[], dict[str, Any] | None]) -> bool:
    env = os.getenv(REMOTE_SYNC_ENV)
    if env is not None and env.strip() != "":
        return env.strip().lower() in _TRUTHY
    return _truthy(_validated_scalar(_required_stored(stored).get("enabled"), "enabled"))


def remote_sync_enabled() -> bool:
    """Whether sync is on in the environment or the stored settings file."""
    return _enabled(_lazy_stored())


def load_remote_sync_config() -> RemoteSyncConfig | None:
    """Settings when sync is on, otherwise ``None``.

    Naming a bucket is not enough — the switch has to be on too, so an
    exported bucket left over from another tool never starts uploading.
    """
    stored = _lazy_stored()
    if not _enabled(stored):
        return None
    if not os.getenv(REMOTE_SYNC_BUCKET_ENV, "").strip():
        _required_stored(stored)
    bucket = _env_or_stored(REMOTE_SYNC_BUCKET_ENV, "bucket", stored)
    if not bucket:
        raise RemoteSyncConfigError(
            f"{REMOTE_SYNC_ENV} is on but {REMOTE_SYNC_BUCKET_ENV} names no bucket"
        )
    prefix = _env_or_stored(REMOTE_SYNC_PREFIX_ENV, "prefix", stored) or DEFAULT_REMOTE_SYNC_PREFIX
    provider = (
        _env_or_stored(REMOTE_SYNC_PROVIDER_ENV, "provider", stored).lower()
        or DEFAULT_REMOTE_SYNC_PROVIDER
    )
    return RemoteSyncConfig(
        bucket=bucket,
        provider=provider,
        prefix=prefix,
        region=_env_or_stored(REMOTE_SYNC_REGION_ENV, "region", stored),
        profile=_env_or_stored(REMOTE_SYNC_PROFILE_ENV, "profile", stored),
        exclude=_exclusions(stored),
    )


__all__ = [
    "RemoteSyncConfig",
    "load_remote_sync_config",
    "remote_sync_enabled",
    "stored_remote_sync_value",
]
