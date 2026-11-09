"""S3 backend for remote sync — one registered :class:`ObjectStore` implementation."""

from __future__ import annotations

from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.enums import BuiltInProvider, RemoteSyncField
from infrastructure.filestorage.errors import RemoteSyncUnavailableError
from infrastructure.filestorage.exposure import PublicAccessStatus
from infrastructure.filestorage.providers._s3_shared import (
    S3ListingMixin,
    s3_check_public_access,
    s3_reason,
)
from infrastructure.filestorage.providers.registry import SetupExtraField, register_object_store

_SERVER_SIDE_ENCRYPTION = "AES256"
PROVIDER_NAME = BuiltInProvider.AWS
CREDENTIAL_HINT = "AWS credentials come from the usual places (env, profile, or SSO)."
EXTRA_FIELDS = (
    SetupExtraField(RemoteSyncField.PROFILE, "Credentials profile (blank if unused)"),
    SetupExtraField(RemoteSyncField.REGION, "Region (blank if unused)"),
)


class S3ObjectStore(S3ListingMixin):
    """Reads and writes objects under one bucket and prefix."""

    def __init__(self, config: RemoteSyncConfig, *, client: Any | None = None) -> None:
        self._config = config
        self._client = client if client is not None else _build_client(config)

    def describe(self) -> str:
        return f"s3://{self._config.bucket}/{self._config.prefix}"

    def put_object(self, key: str, data: bytes) -> None:
        try:
            self._client.put_object(
                Bucket=self._config.bucket,
                Key=self._config.key_for(key),
                Body=data,
                ServerSideEncryption=_SERVER_SIDE_ENCRYPTION,
            )
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(f"cannot write {key} — {s3_reason(exc)}") from exc


def _build_client(config: RemoteSyncConfig) -> Any:
    try:
        # Empty means "use the ambient AWS configuration", which boto3 spells None.
        session = boto3.Session(
            profile_name=config.profile or None,
            region_name=config.region or None,
        )
        return session.client("s3")
    except (BotoCoreError, ClientError, ValueError) as exc:
        raise RemoteSyncUnavailableError(f"cannot build an S3 client — {s3_reason(exc)}") from exc


def _factory(config: RemoteSyncConfig) -> S3ObjectStore:
    return S3ObjectStore(config)


def check_public_access(
    config: RemoteSyncConfig, *, client: Any | None = None
) -> PublicAccessStatus:
    """Ask S3 whether ``config.bucket`` is publicly readable.

    See :func:`~infrastructure.filestorage.providers._s3_shared.s3_check_public_access`
    for the full contract (policy-only, ACL-blind, degrades to ``UNKNOWN``).
    """
    return s3_check_public_access(config, build_client=_build_client, client=client)


# Above the shared default: the boto3 low-level client is safe to share across
# threads, botocore retries throttling itself, and S3 sustains far more
# concurrent writes per prefix than a laptop's session tree will ever produce.
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
    "S3ObjectStore",
    "check_public_access",
]
