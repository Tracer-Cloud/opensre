"""Public-bucket warning: the AWS checker, the registry seam, and the surfaces.

The security property under test is that a public store is loud (``status``
warns) and everything short of that is quiet but truthful — a missing
permission or an unreachable store never fails ``status``, and never echoes
raw exception text into output that a gateway chat surface could relay.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
import requests
from botocore.exceptions import ClientError, EndpointConnectionError

from config.constants.vercel import VERCEL_API_TOKEN_ENV
from infrastructure.filestorage.config import RemoteSyncConfig
from infrastructure.filestorage.enums import BucketExposure, SyncRootName
from infrastructure.filestorage.exposure import PublicAccessStatus
from infrastructure.filestorage.messages import format_exposure_line, format_status_lines
from infrastructure.filestorage.operations import SyncRootStatus, SyncStatus, get_sync_status
from infrastructure.filestorage.providers.aws import check_public_access as aws_check_public_access
from infrastructure.filestorage.providers.azure import (
    check_public_access as azure_check_public_access,
)
from infrastructure.filestorage.providers.gcs import check_public_access as gcs_check_public_access
from infrastructure.filestorage.providers.registry import (
    check_bucket_exposure,
    register_object_store,
    unregister_object_store,
)
from infrastructure.filestorage.providers.vercel import (
    check_public_access as vercel_check_public_access,
)


class _S3Client:
    """Fake boto3 S3 client exposing only ``get_bucket_policy_status``."""

    def __init__(self, *, response: dict | None = None, error: Exception | None = None) -> None:
        self._response = response
        self._error = error

    def get_bucket_policy_status(self, Bucket: str) -> dict:  # noqa: N803, ARG002 - boto3 kwarg casing
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _client_error(code: str) -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "irrelevant"}}, "GetBucketPolicyStatus")


# ── aws.check_public_access ─────────────────────────────────────────────────


def test_public_policy_status_is_reported_public() -> None:
    client = _S3Client(response={"PolicyStatus": {"IsPublic": True}})
    result = aws_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result == PublicAccessStatus(BucketExposure.PUBLIC)


def test_private_policy_status_is_reported_private() -> None:
    client = _S3Client(response={"PolicyStatus": {"IsPublic": False}})
    result = aws_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result == PublicAccessStatus(BucketExposure.PRIVATE)


def test_no_bucket_policy_is_reported_private() -> None:
    client = _S3Client(error=_client_error("NoSuchBucketPolicy"))
    result = aws_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result == PublicAccessStatus(BucketExposure.PRIVATE)


def test_missing_permission_degrades_to_a_note_not_an_error() -> None:
    client = _S3Client(error=_client_error("AccessDenied"))
    result = aws_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result.exposure is BucketExposure.UNKNOWN
    assert "s3:GetBucketPolicyStatus" in result.detail


def test_other_client_error_degrades_without_leaking_its_message() -> None:
    """A gateway chat surface can echo ``detail`` verbatim — it must not carry AWS-side text."""
    client = _S3Client(error=_client_error("InternalError"))
    result = aws_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result.exposure is BucketExposure.UNKNOWN
    assert "irrelevant" not in result.detail
    assert "ClientError" in result.detail


def test_transport_failure_degrades_to_unknown() -> None:
    client = _S3Client(error=EndpointConnectionError(endpoint_url="https://s3.example"))
    result = aws_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result.exposure is BucketExposure.UNKNOWN


def test_client_build_failure_degrades_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even a totally unreachable AWS session must not raise out of the checker."""
    from infrastructure.filestorage.providers.aws import RemoteSyncUnavailableError

    def _raise_client(_config: RemoteSyncConfig) -> object:
        raise RemoteSyncUnavailableError("cannot build an S3 client — boom")

    monkeypatch.setattr("infrastructure.filestorage.providers.aws._build_client", _raise_client)
    result = aws_check_public_access(RemoteSyncConfig(bucket="b"))
    assert result.exposure is BucketExposure.UNKNOWN


# ── azure.check_public_access ───────────────────────────────────────────────


class _AzureResponse:
    """Fake ``httpx.Response`` exposing only what the Azure checker touches."""

    def __init__(self, *, headers: dict[str, str] | None = None, status_code: int = 200) -> None:
        self.headers = headers or {}
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://fake.blob.core.windows.net/")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )


class _AzureClient:
    """Fake ``httpx.Client`` exposing only ``get``."""

    def __init__(
        self, response: _AzureResponse | None = None, *, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error

    def get(
        self,
        _url: str,
        headers: dict[str, str] | None = None,  # noqa: ARG002
        params: dict[str, str] | None = None,  # noqa: ARG002
    ) -> _AzureResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    def close(self) -> None:
        pass


def test_azure_missing_account_name_degrades_to_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unconfigured account name must degrade gracefully without attempting auth."""
    monkeypatch.delenv("AZURE_STORAGE_ACCOUNT_NAME", raising=False)
    result = azure_check_public_access(RemoteSyncConfig(bucket="b", provider="azure"))
    assert result.exposure is BucketExposure.UNKNOWN
    assert "AZURE_STORAGE_ACCOUNT_NAME" in result.detail


def test_azure_public_blob_access_is_reported_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setattr(
        "infrastructure.filestorage.providers.azure._get_azure_access_token", lambda: "token"
    )
    client = _AzureClient(_AzureResponse(headers={"x-ms-blob-public-access": "blob"}))
    result = azure_check_public_access(
        RemoteSyncConfig(bucket="b", provider="azure"), client=client
    )
    assert result == PublicAccessStatus(BucketExposure.PUBLIC)


def test_azure_public_container_access_is_reported_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setattr(
        "infrastructure.filestorage.providers.azure._get_azure_access_token", lambda: "token"
    )
    client = _AzureClient(_AzureResponse(headers={"x-ms-blob-public-access": "container"}))
    result = azure_check_public_access(
        RemoteSyncConfig(bucket="b", provider="azure"), client=client
    )
    assert result == PublicAccessStatus(BucketExposure.PUBLIC)


def test_azure_private_access_is_reported_private(monkeypatch: pytest.MonkeyPatch) -> None:
    """Absence of the x-ms-blob-public-access header dictates a private container."""
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setattr(
        "infrastructure.filestorage.providers.azure._get_azure_access_token", lambda: "token"
    )
    client = _AzureClient(_AzureResponse(headers={}))
    result = azure_check_public_access(
        RemoteSyncConfig(bucket="b", provider="azure"), client=client
    )
    assert result == PublicAccessStatus(BucketExposure.PRIVATE)


def test_azure_forbidden_degrades_to_a_note_not_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing RBAC permissions must fail closed to UNKNOWN without crashing."""
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setattr(
        "infrastructure.filestorage.providers.azure._get_azure_access_token", lambda: "token"
    )
    client = _AzureClient(_AzureResponse(status_code=403))
    result = azure_check_public_access(
        RemoteSyncConfig(bucket="b", provider="azure"), client=client
    )
    assert result.exposure is BucketExposure.UNKNOWN
    assert "permission" in result.detail


def test_azure_other_http_error_degrades_without_leaking_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setattr(
        "infrastructure.filestorage.providers.azure._get_azure_access_token", lambda: "token"
    )
    client = _AzureClient(_AzureResponse(status_code=500))
    result = azure_check_public_access(
        RemoteSyncConfig(bucket="b", provider="azure"), client=client
    )
    assert result.exposure is BucketExposure.UNKNOWN
    assert "HTTPStatusError" in result.detail


def test_azure_transport_failure_degrades_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_STORAGE_ACCOUNT_NAME", "testaccount")
    monkeypatch.setattr(
        "infrastructure.filestorage.providers.azure._get_azure_access_token", lambda: "token"
    )
    client = _AzureClient(error=httpx.ConnectError("boom"))
    result = azure_check_public_access(
        RemoteSyncConfig(bucket="b", provider="azure"), client=client
    )
    assert result.exposure is BucketExposure.UNKNOWN


# ── gcs.check_public_access ─────────────────────────────────────────────────


class _GCSResponse:
    """Fake ``requests.Response`` exposing only what the checker touches."""

    def __init__(self, *, body: object = None, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:  # noqa: PLR2004 - mirrors requests' own threshold
            raise requests.HTTPError(f"HTTP {self.status_code}", response=self)

    def json(self) -> object:
        return self._body


class _GCSSession:
    """Fake ``AuthorizedSession`` exposing only ``get``."""

    def __init__(
        self, response: _GCSResponse | None = None, *, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error

    def get(self, _url: str, timeout: float | None = None) -> _GCSResponse:  # noqa: ARG002
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response


def _iam_policy(*bindings: dict) -> dict:
    return {"bindings": list(bindings)}


def test_public_iam_binding_is_reported_public() -> None:
    body = _iam_policy({"role": "roles/storage.objectViewer", "members": ["allUsers"]})
    session = _GCSSession(_GCSResponse(body=body))
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"), session=session)
    assert result == PublicAccessStatus(BucketExposure.PUBLIC)


def test_authenticated_users_binding_is_also_reported_public() -> None:
    """Any Google account is not "your bucket owner" — same posture as AWS's AuthenticatedUsers."""
    body = _iam_policy(
        {"role": "roles/storage.legacyBucketReader", "members": ["allAuthenticatedUsers"]}
    )
    session = _GCSSession(_GCSResponse(body=body))
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"), session=session)
    assert result == PublicAccessStatus(BucketExposure.PUBLIC)


def test_write_only_public_binding_is_not_reported_public() -> None:
    """A public objectCreator grant is a write exposure, not the read exposure this checks."""
    body = _iam_policy({"role": "roles/storage.objectCreator", "members": ["allUsers"]})
    session = _GCSSession(_GCSResponse(body=body))
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"), session=session)
    assert result == PublicAccessStatus(BucketExposure.PRIVATE)


def test_scoped_binding_is_reported_private() -> None:
    body = _iam_policy(
        {"role": "roles/storage.objectViewer", "members": ["serviceAccount:ci@example.iam"]}
    )
    session = _GCSSession(_GCSResponse(body=body))
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"), session=session)
    assert result == PublicAccessStatus(BucketExposure.PRIVATE)


def test_no_bindings_is_reported_private() -> None:
    session = _GCSSession(_GCSResponse(body=_iam_policy()))
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"), session=session)
    assert result == PublicAccessStatus(BucketExposure.PRIVATE)


def test_forbidden_degrades_to_a_note_not_an_error() -> None:
    session = _GCSSession(_GCSResponse(status_code=403))
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"), session=session)
    assert result.exposure is BucketExposure.UNKNOWN
    assert "IAM policy" in result.detail


def test_gcs_other_http_error_degrades_without_leaking_body() -> None:
    session = _GCSSession(_GCSResponse(status_code=500))
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"), session=session)
    assert result.exposure is BucketExposure.UNKNOWN
    assert "HTTPError" in result.detail


def test_gcs_transport_failure_degrades_to_unknown() -> None:
    session = _GCSSession(error=requests.ConnectionError("boom"))
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"), session=session)
    assert result.exposure is BucketExposure.UNKNOWN


def test_gcs_malformed_response_degrades_to_unknown() -> None:
    session = _GCSSession(_GCSResponse(body=["not", "an", "object"]))
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"), session=session)
    assert result.exposure is BucketExposure.UNKNOWN


def test_gcs_session_build_failure_degrades_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    from infrastructure.filestorage.providers.gcs import RemoteSyncUnavailableError

    def _raise_session() -> object:
        raise RemoteSyncUnavailableError("cannot build GCS credentials — boom")

    monkeypatch.setattr("infrastructure.filestorage.providers.gcs._build_session", _raise_session)
    result = gcs_check_public_access(RemoteSyncConfig(bucket="b"))
    assert result.exposure is BucketExposure.UNKNOWN


# ── vercel.check_public_access ──────────────────────────────────────────────

_BLOB_TOKEN = "vercel_blob_rw_TestStoreId_deadbeefsecret"


class _VercelResponse:
    """Fake ``httpx.Response`` exposing only what the checker touches."""

    def __init__(self, *, body: object = None, status_code: int = 200) -> None:
        self._body = body
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:  # noqa: PLR2004 - mirrors httpx's own threshold
            request = httpx.Request("GET", "https://api.vercel.com/v1/storage/stores/x")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=request, response=response
            )

    def json(self) -> object:
        return self._body


class _VercelClient:
    """Fake ``httpx.Client`` exposing only ``get``."""

    def __init__(
        self, response: _VercelResponse | None = None, *, error: Exception | None = None
    ) -> None:
        self._response = response
        self._error = error

    def get(
        self,
        _url: str,
        headers: dict[str, str] | None = None,  # noqa: ARG002
        params: dict[str, str] | None = None,  # noqa: ARG002
    ) -> _VercelResponse:
        if self._error is not None:
            raise self._error
        assert self._response is not None
        return self._response

    def close(self) -> None:
        pass


def test_missing_vercel_api_token_degrades_to_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """VERCEL_API_TOKEN is optional — BLOB_READ_WRITE_TOKEN cannot authenticate this endpoint at all."""
    monkeypatch.delenv(VERCEL_API_TOKEN_ENV, raising=False)
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"))
    assert result.exposure is BucketExposure.UNKNOWN
    assert VERCEL_API_TOKEN_ENV in result.detail


def test_missing_blob_token_degrades_to_a_note(monkeypatch: pytest.MonkeyPatch) -> None:
    """The store id comes from BLOB_READ_WRITE_TOKEN — config.bucket is only a label."""
    monkeypatch.setenv(VERCEL_API_TOKEN_ENV, "a-real-looking-account-token")
    monkeypatch.delenv("BLOB_READ_WRITE_TOKEN", raising=False)
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"))
    assert result.exposure is BucketExposure.UNKNOWN
    assert "BLOB_READ_WRITE_TOKEN" in result.detail


def test_public_store_is_reported_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VERCEL_API_TOKEN_ENV, "account-token")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", _BLOB_TOKEN)
    client = _VercelClient(_VercelResponse(body={"store": {"access": "public"}}))
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result == PublicAccessStatus(BucketExposure.PUBLIC)


def test_team_id_is_forwarded_as_a_query_param(monkeypatch: pytest.MonkeyPatch) -> None:
    """A token spanning several teams needs teamId to resolve the right one."""
    from config.constants.vercel import VERCEL_TEAM_ID_ENV

    monkeypatch.setenv(VERCEL_API_TOKEN_ENV, "account-token")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", _BLOB_TOKEN)
    monkeypatch.setenv(VERCEL_TEAM_ID_ENV, "team_abc123")
    seen_params: list[dict[str, str] | None] = []

    class _RecordingClient(_VercelClient):
        def get(
            self,
            _url: str,
            headers: dict[str, str] | None = None,
            params: dict[str, str] | None = None,
        ) -> _VercelResponse:
            seen_params.append(params)
            return super().get(_url, headers=headers, params=params)

    client = _RecordingClient(_VercelResponse(body={"store": {"access": "private"}}))
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result == PublicAccessStatus(BucketExposure.PRIVATE)
    assert seen_params == [{"teamId": "team_abc123"}]


def test_private_store_is_reported_private(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VERCEL_API_TOKEN_ENV, "account-token")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", _BLOB_TOKEN)
    client = _VercelClient(_VercelResponse(body={"store": {"access": "private"}}))
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result == PublicAccessStatus(BucketExposure.PRIVATE)


def test_blob_token_cannot_authenticate_this_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    """Confirmed live: this endpoint rejects a Blob read-write token outright (403, invalidToken)."""
    monkeypatch.setenv(VERCEL_API_TOKEN_ENV, _BLOB_TOKEN)  # the wrong token type, on purpose
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", _BLOB_TOKEN)
    client = _VercelClient(_VercelResponse(status_code=403))
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result.exposure is BucketExposure.UNKNOWN
    assert VERCEL_API_TOKEN_ENV in result.detail


def test_vercel_other_http_error_degrades_without_leaking_body(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(VERCEL_API_TOKEN_ENV, "account-token")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", _BLOB_TOKEN)
    client = _VercelClient(_VercelResponse(status_code=500))
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result.exposure is BucketExposure.UNKNOWN
    assert "HTTPStatusError" in result.detail


def test_vercel_transport_failure_degrades_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VERCEL_API_TOKEN_ENV, "account-token")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", _BLOB_TOKEN)
    client = _VercelClient(error=httpx.ConnectError("boom"))
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result.exposure is BucketExposure.UNKNOWN


def test_vercel_malformed_response_degrades_to_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(VERCEL_API_TOKEN_ENV, "account-token")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", _BLOB_TOKEN)
    client = _VercelClient(_VercelResponse(body=["not", "an", "object"]))
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result.exposure is BucketExposure.UNKNOWN


def test_vercel_response_missing_access_field_degrades_to_unknown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(VERCEL_API_TOKEN_ENV, "account-token")
    monkeypatch.setenv("BLOB_READ_WRITE_TOKEN", _BLOB_TOKEN)
    client = _VercelClient(_VercelResponse(body={"store": {"status": "available"}}))
    result = vercel_check_public_access(RemoteSyncConfig(bucket="b"), client=client)
    assert result.exposure is BucketExposure.UNKNOWN


# ── providers.registry.check_bucket_exposure ────────────────────────────────


def test_provider_without_a_checker_is_reported_unchecked() -> None:
    result = check_bucket_exposure(RemoteSyncConfig(bucket="b", provider="not-a-real-provider"))
    assert result.exposure is BucketExposure.UNKNOWN
    assert "not-a-real-provider" in result.detail


def _raising_checker(_config: RemoteSyncConfig) -> PublicAccessStatus:
    raise ValueError("a community checker's own secret-shaped failure")


def test_a_checker_that_raises_degrades_without_leaking_its_message() -> None:
    register_object_store(
        "exposure-raises", lambda _cfg: object(), public_access_checker=_raising_checker
    )
    try:
        result = check_bucket_exposure(RemoteSyncConfig(bucket="b", provider="exposure-raises"))
    finally:
        unregister_object_store("exposure-raises")
    assert result.exposure is BucketExposure.UNKNOWN
    assert "secret-shaped" not in result.detail
    assert "ValueError" in result.detail


def test_unregister_drops_the_checker_too() -> None:
    def _ok(_config: RemoteSyncConfig) -> PublicAccessStatus:
        return PublicAccessStatus(BucketExposure.PUBLIC)

    register_object_store("exposure-toggle", lambda _cfg: object(), public_access_checker=_ok)
    before = check_bucket_exposure(RemoteSyncConfig(bucket="b", provider="exposure-toggle"))
    assert before.exposure is BucketExposure.PUBLIC
    unregister_object_store("exposure-toggle")
    result = check_bucket_exposure(RemoteSyncConfig(bucket="b", provider="exposure-toggle"))
    assert result.exposure is BucketExposure.UNKNOWN


def test_replacing_a_provider_without_a_checker_clears_the_old_one() -> None:
    """A stale checker must never survive a factory swap under the same name.

    If it did, ``status`` would query a completely unrelated cloud API (the
    old provider's) and label the *new* backend with the old one's answer.
    """

    def _public(_config: RemoteSyncConfig) -> PublicAccessStatus:
        return PublicAccessStatus(BucketExposure.PUBLIC)

    register_object_store("exposure-swap", lambda _cfg: object(), public_access_checker=_public)
    try:
        before = check_bucket_exposure(RemoteSyncConfig(bucket="b", provider="exposure-swap"))
        assert before.exposure is BucketExposure.PUBLIC

        # Re-register the same name with a different factory and no checker.
        register_object_store("exposure-swap", lambda _cfg: object())
        after = check_bucket_exposure(RemoteSyncConfig(bucket="b", provider="exposure-swap"))
        assert after.exposure is BucketExposure.UNKNOWN
        assert "exposure-swap" in after.detail
    finally:
        unregister_object_store("exposure-swap")


# ── operations.get_sync_status ──────────────────────────────────────────────


def test_status_exposure_is_none_when_sync_is_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from config.constants.filestorage import REMOTE_SYNC_ENV

    monkeypatch.delenv(REMOTE_SYNC_ENV, raising=False)
    status = get_sync_status()
    assert status.exposure is None


def test_status_calls_the_registered_checker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from config.constants import paths
    from config.constants.filestorage import REMOTE_SYNC_BUCKET_ENV, REMOTE_SYNC_ENV
    from infrastructure.filestorage import operations as sync_service

    def _public(_config: RemoteSyncConfig) -> PublicAccessStatus:
        return PublicAccessStatus(BucketExposure.PUBLIC)

    register_object_store("exposure-status", lambda _cfg: object(), public_access_checker=_public)
    try:
        monkeypatch.setattr(paths, "OPENSRE_HOME_DIR", tmp_path)
        monkeypatch.setenv(REMOTE_SYNC_ENV, "1")
        monkeypatch.setenv(REMOTE_SYNC_BUCKET_ENV, "b")
        monkeypatch.setenv("OPENSRE_REMOTE_SYNC_PROVIDER", "exposure-status")
        monkeypatch.setattr(sync_service, "syncable_roots", lambda: ())
        status = get_sync_status()
    finally:
        unregister_object_store("exposure-status")
    assert status.exposure == PublicAccessStatus(BucketExposure.PUBLIC)


# ── messages.format_status_lines / format_exposure_line ────────────────────


def _status(exposure: PublicAccessStatus | None) -> SyncStatus:
    return SyncStatus(
        config=RemoteSyncConfig(bucket="b", provider="aws", prefix="p"),
        roots=(SyncRootStatus(name=SyncRootName.SESSIONS, path=Path("/s"), exists=True),),
        exposure=exposure,
    )


def test_format_status_lines_warns_loudly_on_a_public_bucket() -> None:
    lines = format_status_lines(_status(PublicAccessStatus(BucketExposure.PUBLIC)))
    assert any("WARNING" in line and "publicly readable" in line for line in lines)


def test_format_status_lines_is_quiet_for_a_private_bucket() -> None:
    lines = format_status_lines(_status(PublicAccessStatus(BucketExposure.PRIVATE)))
    assert not any("WARNING" in line for line in lines)
    assert any("private" in line for line in lines)


def test_format_status_lines_omits_the_line_when_exposure_was_never_computed() -> None:
    """Backward compatible: callers that build a bare ``SyncStatus`` see no new line."""
    lines = format_status_lines(_status(None))
    assert not any("Bucket access" in line or "WARNING" in line for line in lines)


def test_format_exposure_line_unknown_includes_the_detail() -> None:
    line = format_exposure_line(PublicAccessStatus(BucketExposure.UNKNOWN, "missing permission"))
    assert "missing permission" in line
    assert "could not confirm" in line
