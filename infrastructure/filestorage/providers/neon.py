"""Neon Object Storage backend for remote sync.

Neon Object Storage is S3-compatible and branch-scoped: each Neon branch has its
own path-style endpoint (``https://<branch>.storage.<cell>.<region>.aws.neon.tech``)
and its own buckets. This provider is the S3-compatible store with two Neon
rules on top:

- An endpoint is required. Without one, a plain S3 client would quietly talk to
  AWS instead, so the store fails closed with a pointer to ``neon env pull``.
- The endpoint and keys come from the environment ``neon env pull`` writes
  (``AWS_ENDPOINT_URL_S3``, ``AWS_ACCESS_KEY_ID``, ``AWS_SECRET_ACCESS_KEY``,
  ``AWS_REGION``), or ``OPENSRE_REMOTE_SYNC_ENDPOINT_URL`` and a credential from
  ``neon credentials create``. opensre never stores them.

Bucket exposure comes from ``GetBucketPolicyStatus``, which Neon answers from
the bucket's access level (``public_read`` reports public).
"""

from __future__ import annotations

from typing import Any

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.enums import BucketExposure, BuiltInProvider, RemoteSyncField
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.exposure import PublicAccessStatus
from infrastructure.filestorage.providers._s3_shared import s3_check_public_access
from infrastructure.filestorage.providers.registry import SetupExtraField, register_object_store
from infrastructure.filestorage.providers.s3compat import (
    S3CompatObjectStore,
    resolve_endpoint_url,
)

PROVIDER_NAME = BuiltInProvider.NEON
CREDENTIAL_HINT = (
    "Neon Object Storage credentials come from the environment: run `neon env pull` "
    "(AWS_ENDPOINT_URL_S3, AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY), or set "
    "OPENSRE_REMOTE_SYNC_ENDPOINT_URL plus a key from `neon credentials create`."
)
MISSING_ENDPOINT_MESSAGE = (
    "Neon remote sync needs the branch's storage endpoint. Run `neon env pull` to set "
    "AWS_ENDPOINT_URL_S3, or set OPENSRE_REMOTE_SYNC_ENDPOINT_URL to the s3_endpoint "
    "shown by `neon api /projects/<project_id>/branches/<branch_id>/storage`."
)
EXTRA_FIELDS = (SetupExtraField(RemoteSyncField.REGION, "Region (blank to use AWS_REGION)"),)
# Same ceiling as the generic S3-compatible store: boto3 retries throttled writes.
MAX_PARALLEL_UPLOADS = 16
_UNSUPPORTED_POLICY_STATUS_CODES = frozenset({"NotImplemented", "MethodNotAllowed"})


class NeonObjectStore(S3CompatObjectStore):
    """Reads and writes objects under one Neon Object Storage bucket and prefix."""

    def __init__(
        self,
        config: RemoteSyncConfig,
        *,
        endpoint_url: str | None = None,
        client: Any | None = None,
    ) -> None:
        resolved = resolve_endpoint_url(endpoint_url)
        if not resolved:
            raise RemoteSyncConfigError(MISSING_ENDPOINT_MESSAGE)
        super().__init__(config, endpoint_url=resolved, client=client)

    def describe(self) -> str:
        return f"neon://{self._config.bucket}/{self._config.prefix}"


def _factory(config: RemoteSyncConfig) -> NeonObjectStore:
    return NeonObjectStore(config)


def check_public_access(
    config: RemoteSyncConfig, *, client: Any | None = None
) -> PublicAccessStatus:
    """Ask Neon whether ``config.bucket`` is publicly readable (``public_read``)."""
    if client is None and not resolve_endpoint_url():
        return PublicAccessStatus(BucketExposure.UNKNOWN, "no Neon storage endpoint configured")
    return s3_check_public_access(
        config,
        build_client=lambda cfg: NeonObjectStore(cfg)._client,
        client=client,
        unsupported_codes=_UNSUPPORTED_POLICY_STATUS_CODES,
        no_policy_status=PublicAccessStatus(BucketExposure.PRIVATE),
    )


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
    "MISSING_ENDPOINT_MESSAGE",
    "PROVIDER_NAME",
    "NeonObjectStore",
    "check_public_access",
]
