"""S3-compatible backend for remote sync (MinIO, Cloudflare R2, DigitalOcean Spaces).

One registered :class:`~infrastructure.filestorage.contracts.ObjectStore` implementation.
Configures path-style addressing and custom endpoint URLs for non-AWS S3 stores.
"""

from __future__ import annotations

import os
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from config.constants.filestorage import REMOTE_SYNC_ENDPOINT_URL_ENV
from infrastructure.filestorage.config import RemoteSyncConfig, stored_remote_sync_value
from infrastructure.filestorage.enums import BucketExposure, BuiltInProvider, RemoteSyncField
from infrastructure.filestorage.errors import RemoteSyncUnavailableError
from infrastructure.filestorage.exposure import PublicAccessStatus
from infrastructure.filestorage.providers._s3_shared import (
    S3ListingMixin,
    s3_check_public_access,
    s3_reason,
)
from infrastructure.filestorage.providers.registry import SetupExtraField, register_object_store

PROVIDER_NAME = BuiltInProvider.S3COMPAT
CREDENTIAL_HINT = (
    "S3-compatible credentials come from ambient environment "
    "(AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY, profile, or OPENSRE_REMOTE_SYNC_ENDPOINT_URL)."
)
EXTRA_FIELDS = (
    SetupExtraField(RemoteSyncField.PROFILE, "Credentials profile (blank if unused)"),
    SetupExtraField(RemoteSyncField.REGION, "Region (blank if unused)"),
)
_UNSUPPORTED_POLICY_STATUS_CODES = frozenset(
    {"MethodNotAllowed", "NotImplemented", "InvalidRequest", "UnsupportedOperation"}
)


class S3CompatObjectStore(S3ListingMixin):
    """Reads and writes objects under one S3-compatible bucket and prefix."""

    def __init__(
        self,
        config: RemoteSyncConfig,
        *,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        self._config = config
        self._endpoint_url = _resolve_endpoint_url(endpoint_url)
        self._client = client if client is not None else _build_client(config, self._endpoint_url)

    def describe(self) -> str:
        return f"s3compat://{self._config.bucket}/{self._config.prefix}"

    def put_object(self, key: str, data: bytes) -> None:
        try:
            self._client.put_object(
                Bucket=self._config.bucket,
                Key=self._config.key_for(key),
                Body=data,
            )
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(f"cannot write {key} — {s3_reason(exc)}") from exc


def _resolve_endpoint_url(explicit: str | None = None) -> str | None:
    """The endpoint URL to use, else ``None`` for the built-in AWS endpoint.

    Checked in order: the ``explicit`` argument (tests / direct callers), the
    environment (``OPENSRE_REMOTE_SYNC_ENDPOINT_URL``, then the AWS-native
    ``AWS_ENDPOINT_URL_S3``/``AWS_ENDPOINT_URL``), then ``~/.opensre/config.yml``
    ``remote_sync.endpoint_url`` — the same env-then-stored precedence every
    other remote-sync setting (``bucket``, ``region``, ``profile``, …) follows
    in :mod:`infrastructure.filestorage.config`, so a value set once via ``opensre
    remote-sync setup`` keeps working across runs instead of only holding for
    a shell session that exported the variable.
    """
    if explicit and explicit.strip():
        return explicit.strip()
    env_endpoint = (
        os.getenv(REMOTE_SYNC_ENDPOINT_URL_ENV, "").strip()
        or os.getenv("AWS_ENDPOINT_URL_S3", "").strip()
        or os.getenv("AWS_ENDPOINT_URL", "").strip()
    )
    if env_endpoint:
        return env_endpoint
    return stored_remote_sync_value("endpoint_url") or None


def _build_client(config: RemoteSyncConfig, endpoint_url: str | None) -> Any:
    """A boto3 S3 client for ``config``, using ``endpoint_url`` as-is (already resolved).

    Takes the endpoint as a required argument rather than resolving it itself,
    so a caller that already has a resolved value (``__init__`` caches one on
    ``self._endpoint_url``) never pays for a second env/config-file lookup.
    """
    try:
        session = boto3.Session(
            profile_name=config.profile or None,
            region_name=config.region or None,
        )
        client_config = Config(s3={"addressing_style": "path"})
        return session.client(
            "s3",
            endpoint_url=endpoint_url,
            config=client_config,
        )
    except (BotoCoreError, ClientError, ValueError) as exc:
        raise RemoteSyncUnavailableError(
            f"cannot build an S3-compatible client — {s3_reason(exc)}"
        ) from exc


def _factory(config: RemoteSyncConfig) -> S3CompatObjectStore:
    return S3CompatObjectStore(config)


def check_public_access(
    config: RemoteSyncConfig, *, client: Any | None = None
) -> PublicAccessStatus:
    """Ask the store whether ``config.bucket`` is publicly readable.

    See :func:`~infrastructure.filestorage.providers._s3_shared.s3_check_public_access`
    for the full contract (policy-only, ACL-blind, degrades to ``UNKNOWN``).
    Unlike AWS S3, a missing bucket policy here reports ``UNKNOWN`` rather
    than ``PRIVATE``: S3-compatible backends (MinIO, R2, Spaces, …) don't
    share AWS's Block Public Access defaults, so no policy doesn't rule out a
    public-read ACL. Also treats ``MethodNotAllowed``/``NotImplemented``/
    ``InvalidRequest``/``UnsupportedOperation`` as ``UNKNOWN``: several
    S3-compatible backends don't implement ``GetBucketPolicyStatus`` at all.
    """
    return s3_check_public_access(
        config,
        build_client=lambda cfg: _build_client(cfg, _resolve_endpoint_url()),
        client=client,
        unsupported_codes=_UNSUPPORTED_POLICY_STATUS_CODES,
        no_policy_status=PublicAccessStatus(BucketExposure.UNKNOWN, "no bucket policy present"),
    )


MAX_PARALLEL_UPLOADS = 16

register_object_store(
    PROVIDER_NAME,
    _factory,
    credential_hint=CREDENTIAL_HINT,
    extra_fields=EXTRA_FIELDS,
    public_access_checker=check_public_access,
    max_parallel_uploads=MAX_PARALLEL_UPLOADS,
)

__all__ = [
    "CREDENTIAL_HINT",
    "EXTRA_FIELDS",
    "MAX_PARALLEL_UPLOADS",
    "PROVIDER_NAME",
    "S3CompatObjectStore",
    "check_public_access",
]
