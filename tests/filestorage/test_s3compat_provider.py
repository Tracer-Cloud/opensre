"""Tests for the S3-compatible ObjectStore provider (MinIO / R2 / Spaces).

Follows TDD and AAA (Arrange -> Act -> Assert) structure without live cloud calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from botocore.exceptions import ClientError

from config.constants.filestorage import REMOTE_SYNC_ENDPOINT_URL_ENV
from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.enums import BucketExposure, BuiltInProvider, RemoteSyncField
from infrastructure.filestorage.errors import RemoteSyncUnavailableError
from infrastructure.filestorage.providers.registry import (
    credential_hint_for_provider,
    provider_extra_fields,
    registered_providers,
)
from infrastructure.filestorage.providers.s3compat import (
    S3CompatObjectStore,
    check_public_access,
)


class _FakeS3Client:
    """In-memory fake boto3 S3 client supporting custom endpoint and path-style addressing."""

    def __init__(self, endpoint_url: str | None = None) -> None:
        self.endpoint_url = endpoint_url
        self.objects: dict[tuple[str, str], bytes] = {}
        self.meta = type("Meta", (), {"endpoint_url": endpoint_url})()

    def put_object(self, Bucket: str, Key: str, Body: bytes) -> dict[str, Any]:
        self.objects[(Bucket, Key)] = Body
        return {"ResponseMetadata": {"HTTPStatusCode": 200}}

    def get_object(self, Bucket: str, Key: str) -> dict[str, Any]:
        if (Bucket, Key) not in self.objects:
            raise ClientError(
                {"Error": {"Code": "NoSuchKey", "Message": "The specified key does not exist."}},
                "GetObject",
            )
        data = self.objects[(Bucket, Key)]

        class _BodyStream:
            def __init__(self, content: bytes) -> None:
                self._content = content

            def read(self) -> bytes:
                return self._content

        return {"Body": _BodyStream(data)}

    def get_paginator(self, _operation_name: str) -> Any:
        client_self = self

        class _Paginator:
            def paginate(self, Bucket: str, Prefix: str) -> list[dict[str, Any]]:
                matched = []
                for (b, k), data in client_self.objects.items():
                    if b == Bucket and k.startswith(Prefix):
                        matched.append(
                            {
                                "Key": k,
                                "Size": len(data),
                                "LastModified": "2026-08-12T12:00:00Z",
                                "ETag": '"fake-etag"',
                            }
                        )
                return [{"Contents": matched}]

        return _Paginator()

    def get_bucket_policy_status(self, Bucket: str) -> dict[str, Any]:  # noqa: ARG002
        return {"PolicyStatus": {"IsPublic": False}}


def test_s3compat_describe_returns_descriptive_target() -> None:
    # Arrange
    config = RemoteSyncConfig(bucket="my-bucket", provider="s3compat", prefix="opensre")
    client = _FakeS3Client(endpoint_url="http://localhost:9000")
    store = S3CompatObjectStore(config, client=client)

    # Act
    description = store.describe()

    # Assert
    assert description == "s3compat://my-bucket/opensre"


def test_s3compat_put_get_list_objects() -> None:
    # Arrange
    config = RemoteSyncConfig(bucket="my-minio-bucket", provider="s3compat", prefix="opensre")
    client = _FakeS3Client(endpoint_url="http://minio.local:9000")
    store = S3CompatObjectStore(config, client=client)

    # Act: put object
    store.put_object("sessions/1.jsonl", b'{"data": 1}\n')

    # Act: list objects
    objects = store.list_objects("sessions")

    # Act: get object
    content = store.get_object("sessions/1.jsonl")

    # Assert
    assert len(objects) == 1
    assert objects[0].key == "sessions/1.jsonl"
    assert objects[0].size == len(b'{"data": 1}\n')
    assert content == b'{"data": 1}\n'


def test_s3compat_resolves_endpoint_url_from_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Arrange
    monkeypatch.setenv(REMOTE_SYNC_ENDPOINT_URL_ENV, "https://r2.cloudflare.com")
    config = RemoteSyncConfig(bucket="r2-bucket", provider="s3compat", prefix="opensre")

    # Act
    store = S3CompatObjectStore(config, client=_FakeS3Client("https://r2.cloudflare.com"))

    # Assert
    assert store._endpoint_url == "https://r2.cloudflare.com"


def test_s3compat_resolves_endpoint_url_from_stored_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A ``config.yml``-persisted endpoint keeps working without a shell export."""
    # Arrange
    from config.constants import paths
    from config.local_settings import update_section

    monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
    monkeypatch.delenv(REMOTE_SYNC_ENDPOINT_URL_ENV, raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL_S3", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    update_section(
        "remote_sync",
        {"enabled": True, "bucket": "my-minio-bucket", "endpoint_url": "http://localhost:9000"},
    )
    config = RemoteSyncConfig(bucket="my-minio-bucket", provider="s3compat", prefix="opensre")

    # Act
    store = S3CompatObjectStore(config, client=_FakeS3Client("http://localhost:9000"))

    # Assert
    assert store._endpoint_url == "http://localhost:9000"


def test_s3compat_list_objects_error_raises_remote_sync_unavailable() -> None:
    # Arrange
    class _FailingClient:
        def get_paginator(self, _name: str) -> Any:
            raise ClientError(
                {"Error": {"Code": "AccessDenied", "Message": "Access Denied"}},
                "ListObjectsV2",
            )

    config = RemoteSyncConfig(bucket="private-bucket", provider="s3compat", prefix="opensre")
    store = S3CompatObjectStore(config, client=_FailingClient())

    # Act & Assert
    with pytest.raises(RemoteSyncUnavailableError) as caught:
        store.list_objects("")

    assert "cannot list s3compat://private-bucket/opensre" in str(caught.value)
    assert "AccessDenied" in str(caught.value)


def test_s3compat_get_object_error_raises_remote_sync_unavailable() -> None:
    # Arrange
    config = RemoteSyncConfig(bucket="bucket", provider="s3compat", prefix="opensre")
    store = S3CompatObjectStore(config, client=_FakeS3Client())

    # Act & Assert
    with pytest.raises(RemoteSyncUnavailableError) as caught:
        store.get_object("nonexistent.txt")

    assert "cannot read nonexistent.txt" in str(caught.value)


def test_s3compat_check_public_access_returns_private() -> None:
    # Arrange
    config = RemoteSyncConfig(bucket="minio-bucket", provider="s3compat", prefix="opensre")
    client = _FakeS3Client(endpoint_url="http://localhost:9000")

    # Act
    status = check_public_access(config, client=client)

    # Assert
    assert status.exposure == BucketExposure.PRIVATE
    assert status.detail == ""


def test_s3compat_check_public_access_no_policy_returns_unknown() -> None:
    # Arrange
    class _NoPolicyClient:
        def get_bucket_policy_status(self, Bucket: str) -> dict[str, Any]:  # noqa: ARG002
            raise ClientError(
                {
                    "Error": {
                        "Code": "NoSuchBucketPolicy",
                        "Message": "The bucket policy does not exist",
                    }
                },
                "GetBucketPolicyStatus",
            )

    config = RemoteSyncConfig(bucket="minio-bucket", provider="s3compat", prefix="opensre")

    # Act
    status = check_public_access(config, client=_NoPolicyClient())

    # Assert
    assert status.exposure == BucketExposure.UNKNOWN
    assert "no bucket policy" in status.detail


@pytest.mark.parametrize(
    "code", ["MethodNotAllowed", "NotImplemented", "InvalidRequest", "UnsupportedOperation"]
)
def test_s3compat_check_public_access_unsupported_api_returns_unknown(code: str) -> None:
    # Arrange
    class _UnsupportedClient:
        def get_bucket_policy_status(self, Bucket: str) -> dict[str, Any]:  # noqa: ARG002
            raise ClientError(
                {"Error": {"Code": code, "Message": "The specified method is not allowed."}},
                "GetBucketPolicyStatus",
            )

    config = RemoteSyncConfig(bucket="r2-bucket", provider="s3compat", prefix="opensre")

    # Act
    status = check_public_access(config, client=_UnsupportedClient())

    # Assert
    assert status.exposure == BucketExposure.UNKNOWN
    assert "not supported" in status.detail


def test_s3compat_provider_registration() -> None:
    # Arrange & Act
    providers = registered_providers()
    fields = provider_extra_fields("s3compat")
    hint = credential_hint_for_provider("s3compat")

    # Assert
    assert BuiltInProvider.S3COMPAT.value in providers
    assert RemoteSyncField.REGION in {f.field for f in fields}
    assert RemoteSyncField.PROFILE in {f.field for f in fields}
    assert "S3-compatible" in hint
