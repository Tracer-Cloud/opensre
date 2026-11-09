"""Neon Object Storage remote-sync provider.

Covers what differs from the generic S3-compatible store: the endpoint is
mandatory (no silent fallback to AWS), every endpoint source is honored in the
documented order, and exposure comes from ``GetBucketPolicyStatus``, which Neon
answers from the bucket's access level. No live calls.
"""

from __future__ import annotations

from typing import Any

import pytest
from botocore.exceptions import ClientError

from config.constants.filestorage import REMOTE_SYNC_ENDPOINT_URL_ENV
from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.enums import BucketExposure, BuiltInProvider
from infrastructure.filestorage.errors import RemoteSyncConfigError
from infrastructure.filestorage.providers.neon import (
    MISSING_ENDPOINT_MESSAGE,
    NeonObjectStore,
    check_public_access,
)
from infrastructure.filestorage.providers.registry import (
    build_object_store,
    builtin_providers,
    credential_hint_for_provider,
    max_parallel_uploads_for_provider,
)

NEON_ENDPOINT = "https://br-summer-art-bdbz1xg2.storage.c-8.us-east-2.aws.neon.tech"
_ENDPOINT_ENVS = (REMOTE_SYNC_ENDPOINT_URL_ENV, "AWS_ENDPOINT_URL_S3", "AWS_ENDPOINT_URL")


@pytest.fixture(autouse=True)
def _no_ambient_endpoint(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    for name in _ENDPOINT_ENVS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("OPENSRE_HOME", str(tmp_path))
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "nak_live_test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test-secret")


def _config(bucket: str = "incident-artifacts") -> RemoteSyncConfig:
    return RemoteSyncConfig(bucket=bucket, provider=BuiltInProvider.NEON, prefix="opensre")


class _PolicyClient:
    def __init__(self, *, is_public: bool | None = None, error_code: str = "") -> None:
        self.is_public = is_public
        self.error_code = error_code

    def get_bucket_policy_status(self, Bucket: str) -> dict[str, Any]:  # noqa: ARG002, N803
        if self.error_code:
            raise ClientError({"Error": {"Code": self.error_code}}, "GetBucketPolicyStatus")
        return {"PolicyStatus": {"IsPublic": self.is_public}}


def test_neon_is_a_builtin_provider_with_its_own_hint() -> None:
    assert "neon" in builtin_providers()
    assert "neon env pull" in credential_hint_for_provider("neon")
    assert max_parallel_uploads_for_provider("neon") == 16


def test_missing_endpoint_fails_closed_instead_of_falling_back_to_aws() -> None:
    with pytest.raises(RemoteSyncConfigError) as err:
        build_object_store(_config())
    assert str(err.value) == MISSING_ENDPOINT_MESSAGE


@pytest.mark.parametrize(
    ("env", "expected"),
    [
        ({"AWS_ENDPOINT_URL_S3": NEON_ENDPOINT}, NEON_ENDPOINT),
        ({"AWS_ENDPOINT_URL": NEON_ENDPOINT}, NEON_ENDPOINT),
        (
            {REMOTE_SYNC_ENDPOINT_URL_ENV: NEON_ENDPOINT, "AWS_ENDPOINT_URL_S3": "https://other"},
            NEON_ENDPOINT,
        ),
    ],
)
def test_endpoint_sources_follow_documented_precedence(
    monkeypatch: pytest.MonkeyPatch, env: dict[str, str], expected: str
) -> None:
    for name, value in env.items():
        monkeypatch.setenv(name, value)
    store = build_object_store(_config())
    assert isinstance(store, NeonObjectStore)
    client = store._client
    assert client.meta.endpoint_url == expected
    assert client.meta.config.s3["addressing_style"] == "path"


def test_explicit_endpoint_argument_wins_and_describe_names_neon(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AWS_ENDPOINT_URL_S3", "https://ignored.example")
    store = NeonObjectStore(_config(), endpoint_url=NEON_ENDPOINT)
    assert store._client.meta.endpoint_url == NEON_ENDPOINT
    assert store.describe() == "neon://incident-artifacts/opensre"


@pytest.mark.parametrize(
    ("client", "exposure"),
    [
        (_PolicyClient(is_public=True), BucketExposure.PUBLIC),
        (_PolicyClient(is_public=False), BucketExposure.PRIVATE),
        (_PolicyClient(error_code="AccessDenied"), BucketExposure.UNKNOWN),
        (_PolicyClient(error_code="NotImplemented"), BucketExposure.UNKNOWN),
    ],
)
def test_exposure_reflects_neon_access_level(client: _PolicyClient, exposure: str) -> None:
    assert check_public_access(_config(), client=client).exposure == exposure


def test_exposure_without_endpoint_is_unknown_not_an_aws_call() -> None:
    status = check_public_access(_config())
    assert status.exposure == BucketExposure.UNKNOWN
    assert "endpoint" in status.detail
