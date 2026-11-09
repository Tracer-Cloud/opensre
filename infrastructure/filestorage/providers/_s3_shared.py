"""Boto3 S3 plumbing shared by ``aws.py`` and ``s3compat.py``.

Not a registered provider itself. AWS S3 and S3-compatible stores (MinIO, R2,
Spaces, …) talk to the same boto3 ``s3`` client surface and differ only in
client construction (endpoint URL, addressing style, server-side encryption
support) and which ``ClientError`` codes a ``GetBucketPolicyStatus`` call can
return. Everything else — listing/pagination, key stripping, and error
translation — is factored out here so a fix lands once instead of drifting
between two copies.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from botocore.exceptions import BotoCoreError, ClientError

from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.contracts import RemoteObject
from infrastructure.filestorage.enums import BucketExposure
from infrastructure.filestorage.errors import RemoteSyncUnavailableError
from infrastructure.filestorage.exposure import PublicAccessStatus

if TYPE_CHECKING:
    from collections.abc import Iterator


def s3_reason(exc: Exception) -> str:
    """The provider-side cause, for a local operator to act on."""
    return f"{type(exc).__name__}: {exc}"


class S3ListingMixin:
    """``list_objects``/``get_object``/pagination/key-stripping for an S3-family store.

    Expects the including class to set ``self._config`` (:class:`RemoteSyncConfig`)
    and ``self._client`` (a boto3 ``s3`` client) in its own ``__init__``, and to
    override ``describe()``.
    """

    _config: RemoteSyncConfig
    _client: Any

    def describe(self) -> str:
        """Human-facing ``scheme://bucket/prefix`` target for error messages."""
        raise NotImplementedError

    def list_objects(self, prefix: str) -> list[RemoteObject]:
        full_prefix = (
            self._config.key_for(prefix) if prefix else f"{self._config.prefix.rstrip('/')}/"
        )
        out: list[RemoteObject] = []
        try:
            for page in self._pages(full_prefix):
                for item in page.get("Contents", []):
                    key = str(item["Key"])
                    out.append(
                        RemoteObject(
                            key=self._strip_prefix(key),
                            size=int(item.get("Size", 0)),
                            last_modified=item["LastModified"],
                            etag=str(item.get("ETag", "")).strip('"'),
                        )
                    )
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(
                f"cannot list {self.describe()} — {s3_reason(exc)}"
            ) from exc
        return out

    def get_object(self, key: str) -> bytes:
        try:
            response = self._client.get_object(
                Bucket=self._config.bucket, Key=self._config.key_for(key)
            )
            body: bytes = response["Body"].read()
            return body
        except (BotoCoreError, ClientError) as exc:
            raise RemoteSyncUnavailableError(f"cannot read {key} — {s3_reason(exc)}") from exc

    def _pages(self, prefix: str) -> Iterator[dict[str, Any]]:
        paginator = self._client.get_paginator("list_objects_v2")
        yield from paginator.paginate(Bucket=self._config.bucket, Prefix=prefix)

    def _strip_prefix(self, full_key: str) -> str:
        prefix = f"{self._config.prefix.rstrip('/')}/"
        return full_key[len(prefix) :] if full_key.startswith(prefix) else full_key


def s3_check_public_access(
    config: RemoteSyncConfig,
    *,
    build_client: Callable[[RemoteSyncConfig], Any],
    client: Any | None = None,
    unsupported_codes: frozenset[str] = frozenset(),
    no_policy_status: PublicAccessStatus = PublicAccessStatus(BucketExposure.PRIVATE),
) -> PublicAccessStatus:
    """Ask an S3-family store whether ``config.bucket`` is publicly readable.

    Uses ``s3:GetBucketPolicyStatus``, so the result reflects only the bucket
    policy (plus, on real AWS, account/bucket Block Public Access settings
    that factor into ``IsPublic``) — never object or bucket ACLs. There is no
    portable ACL-exposure check across S3-compatible backends, so this stays a
    best-effort policy-only signal rather than a public/private guarantee.

    Degrades to :class:`~infrastructure.filestorage.enums.BucketExposure.UNKNOWN`
    rather than raising: the permission (or the operation itself) can be
    missing on an otherwise perfectly usable bucket, and this check must never
    stand in the way of ``status`` or ``setup`` reporting the rest of what
    they know. ``detail`` never carries the raw exception text for any
    failure but the permission gap — this result can be echoed straight into
    a gateway chat surface (see ``format_status_lines``), and a provider error
    message is not vetted for that (CWE-209).

    ``unsupported_codes`` lets a caller name the ``ClientError`` codes its
    backend returns instead of a real answer when it does not implement
    ``GetBucketPolicyStatus`` at all (e.g. ``MethodNotAllowed`` on MinIO).

    ``no_policy_status`` is what to report for ``NoSuchBucketPolicy`` (no
    policy exists). Defaults to ``PRIVATE`` — "no policy grants public access"
    — which is the right read on real AWS S3, where Block Public Access
    defaults keep ACL-driven exposure closed too. S3-compatible backends
    (MinIO, R2, Spaces, …) do not share that default, so a caller there should
    pass ``PublicAccessStatus(BucketExposure.UNKNOWN, "no bucket policy
    present")`` instead, since the absence of a policy says nothing about
    ACL-driven exposure on those backends.
    """
    try:
        s3 = client if client is not None else build_client(config)
        response = s3.get_bucket_policy_status(Bucket=config.bucket)
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code", "")
        if code == "AccessDenied":
            return PublicAccessStatus(
                BucketExposure.UNKNOWN, "missing the s3:GetBucketPolicyStatus permission"
            )
        if code == "NoSuchBucketPolicy":
            return no_policy_status
        if code in unsupported_codes:
            return PublicAccessStatus(
                BucketExposure.UNKNOWN, "bucket policy status check not supported by endpoint"
            )
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    except (BotoCoreError, ValueError, RemoteSyncUnavailableError) as exc:
        return PublicAccessStatus(BucketExposure.UNKNOWN, f"cannot check ({type(exc).__name__})")
    is_public = bool(response.get("PolicyStatus", {}).get("IsPublic", False))
    return PublicAccessStatus(BucketExposure.PUBLIC if is_public else BucketExposure.PRIVATE)


__all__ = ["S3ListingMixin", "s3_check_public_access", "s3_reason"]
