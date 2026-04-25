"""Tests for app.services.s3_client -- direct unit tests with mocked boto3."""

from __future__ import annotations

from collections.abc import Generator
from datetime import datetime
from unittest.mock import MagicMock, call, patch

import pytest
from botocore.exceptions import ClientError

from app.services import s3_client

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _body(content: bytes) -> MagicMock:
    """Return a mock S3 StreamingBody that spits out *content* on ``read()``."""
    b = MagicMock()
    b.read.return_value = content
    return b


_DT = datetime(2024, 6, 15, 10, 30, 0)
_BUCKET = "test-bucket"
_KEY = "test-key.json"
_ETAG_QUOTED = '"abc123"'  # as AWS returns (wrapping quotes)
_ETAG_CLEAN = "abc123"  # after the module strips quotes


# ---------------------------------------------------------------------------
# Shared fixture -- patches module-level deps so tests never call real AWS
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_s3() -> Generator[MagicMock]:
    """Patch ``make_boto3_client`` and ``require_aws_credentials`` in the
    ``s3_client`` module, returning a ``MagicMock`` that stands in for the real
    boto3 S3 client.

    Tests configure call-return values / side effects on this mock directly.
    """
    client = MagicMock()
    p1 = patch("app.services.s3_client.make_boto3_client", return_value=client)
    p2 = patch("app.services.s3_client.require_aws_credentials", return_value=None)
    p1.start()
    p2.start()
    yield client
    p2.stop()
    p1.stop()


def _client_error(code: str, msg: str = "", op: str = "GetObject") -> ClientError:
    """Build a ``botocore.exceptions.ClientError`` with the given error code."""
    return ClientError({"Error": {"Code": code, "Message": msg}}, op)


# ===================================================================
# head_object (lines 58-101)
# ===================================================================


class TestHeadObject:
    """Coverage: object exists / 404 NoSuchKey / generic ClientError."""

    def test_object_exists(self, mock_s3: MagicMock) -> None:
        mock_s3.head_object.return_value = {
            "ContentLength": 2048,
            "LastModified": _DT,
            "ContentType": "application/json",
            "ETag": _ETAG_QUOTED,
            "VersionId": "v1",
            "StorageClass": "STANDARD",
            "Metadata": {"commit": "abc"},
        }

        result = s3_client.head_object(_BUCKET, _KEY)

        assert result == {
            "success": True,
            "exists": True,
            "data": {
                "bucket": _BUCKET,
                "key": _KEY,
                "size": 2048,
                "last_modified": _DT,
                "content_type": "application/json",
                "etag": _ETAG_CLEAN,
                "version_id": "v1",
                "storage_class": "STANDARD",
                "metadata": {"commit": "abc"},
            },
        }
        mock_s3.head_object.assert_called_once_with(Bucket=_BUCKET, Key=_KEY)

    @pytest.mark.parametrize("code", ["404", "NoSuchKey"])
    def test_not_found(self, mock_s3: MagicMock, code: str) -> None:
        mock_s3.head_object.side_effect = _client_error(code, op="HeadObject")

        result = s3_client.head_object(_BUCKET, _KEY)

        assert result == {
            "success": True,
            "exists": False,
            "data": {"bucket": _BUCKET, "key": _KEY},
        }

    def test_client_error(self, mock_s3: MagicMock) -> None:
        mock_s3.head_object.side_effect = _client_error("Forbidden", op="HeadObject")

        result = s3_client.head_object(_BUCKET, _KEY)

        assert result["success"] is False
        assert "Forbidden" in result["error"]


# ===================================================================
# get_object_sample (lines 120-175)
# ===================================================================


class TestGetObjectSample:
    """Coverage: text / binary / ClientError."""

    def test_text_content(self, mock_s3: MagicMock) -> None:
        body_bytes = b'{"key": "value"}\n'
        mock_s3.get_object.return_value = {
            "Body": _body(body_bytes),
            "ContentType": "application/json",
            "ContentLength": len(body_bytes),
        }

        result = s3_client.get_object_sample(_BUCKET, _KEY, max_bytes=4096)

        assert result["success"] is True
        data = result["data"]
        assert data["is_text"] is True
        assert data["sample"] == '{"key": "value"}\n'
        assert data["sample_bytes"] == len(body_bytes)
        assert data["total_size"] == len(body_bytes)
        assert data["content_type"] == "application/json"
        assert data["sample_raw_hex"] is None
        mock_s3.get_object.assert_called_once_with(Bucket=_BUCKET, Key=_KEY, Range="bytes=0-4095")

    def test_binary_content(self, mock_s3: MagicMock) -> None:
        raw = b"\x00\x01\x02\xff\xfe\xfd"
        mock_s3.get_object.return_value = {
            "Body": _body(raw),
            "ContentType": "application/octet-stream",
            "ContentLength": len(raw),
        }

        result = s3_client.get_object_sample(_BUCKET, _KEY)

        assert result["success"] is True
        data = result["data"]
        assert data["is_text"] is False
        assert data["sample"] is None
        assert data["sample_raw_hex"] == raw.hex()
        assert data["sample_bytes"] == len(raw)

    def test_client_error(self, mock_s3: MagicMock) -> None:
        mock_s3.get_object.side_effect = _client_error("AccessDenied")

        result = s3_client.get_object_sample(_BUCKET, _KEY)

        assert result["success"] is False
        assert "AccessDenied" in result["error"]


# ===================================================================
# get_full_object (lines 178-226)
# ===================================================================


class TestGetFullObject:
    """Coverage: text / binary / NoSuchKey / generic ClientError."""

    def test_text_content(self, mock_s3: MagicMock) -> None:
        body_bytes = b'{"log": "test"}'
        mock_s3.get_object.return_value = {
            "Body": _body(body_bytes),
            "ContentType": "application/json",
            "ContentLength": len(body_bytes),
            "Metadata": {"source": "test"},
        }

        result = s3_client.get_full_object(_BUCKET, _KEY)

        assert result["success"] is True
        data = result["data"]
        assert data["is_text"] is True
        assert data["content"] == '{"log": "test"}'
        assert data["content_type"] == "application/json"
        assert data["size"] == len(body_bytes)
        assert data["bucket"] == _BUCKET
        assert data["key"] == _KEY
        assert data["metadata"] == {"source": "test"}
        mock_s3.get_object.assert_called_once_with(Bucket=_BUCKET, Key=_KEY)

    def test_binary_content(self, mock_s3: MagicMock) -> None:
        raw = b"\xff\xfe\x00\x01"
        mock_s3.get_object.return_value = {
            "Body": _body(raw),
            "ContentType": "application/octet-stream",
            "ContentLength": len(raw),
        }

        result = s3_client.get_full_object(_BUCKET, _KEY)

        assert result["success"] is True
        data = result["data"]
        assert data["is_text"] is False
        assert data["content"] is None

    def test_no_such_key(self, mock_s3: MagicMock) -> None:
        mock_s3.get_object.side_effect = _client_error("NoSuchKey")

        result = s3_client.get_full_object(_BUCKET, _KEY)

        assert result == {
            "success": True,
            "exists": False,
            "data": {"bucket": _BUCKET, "key": _KEY},
        }

    def test_client_error(self, mock_s3: MagicMock) -> None:
        mock_s3.get_object.side_effect = _client_error("InternalError")

        result = s3_client.get_full_object(_BUCKET, _KEY)

        assert result["success"] is False
        assert "InternalError" in result["error"]


# ===================================================================
# list_object_versions (lines 229-297)
# ===================================================================


class TestListObjectVersions:
    """Coverage: versions+delete-markers / empty / ClientError."""

    def test_versions_and_delete_markers(self, mock_s3: MagicMock) -> None:
        mock_s3.list_object_versions.return_value = {
            "Versions": [
                {
                    "Key": _KEY,
                    "VersionId": "v1",
                    "LastModified": _DT,
                    "Size": 100,
                    "ETag": '"etag1"',
                    "IsLatest": True,
                    "StorageClass": "STANDARD",
                },
                {
                    "Key": _KEY,
                    "VersionId": "v2",
                    "LastModified": _DT,
                    "Size": 200,
                    "ETag": '"etag2"',
                    "IsLatest": False,
                    "StorageClass": "GLACIER",
                },
                {
                    "Key": "other-key",  # filtered out
                    "VersionId": "v3",
                    "LastModified": _DT,
                    "Size": 300,
                    "ETag": '"etag3"',
                    "IsLatest": False,
                    "StorageClass": "STANDARD",
                },
            ],
            "DeleteMarkers": [
                {
                    "Key": _KEY,
                    "VersionId": "dm1",
                    "LastModified": _DT,
                    "IsLatest": False,
                },
            ],
        }

        result = s3_client.list_object_versions(_BUCKET, _KEY)

        assert result["success"] is True
        data = result["data"]
        assert data["bucket"] == _BUCKET
        assert data["key"] == _KEY
        assert data["version_count"] == 2
        assert data["versions"] == [
            {
                "version_id": "v1",
                "last_modified": _DT,
                "size": 100,
                "etag": "etag1",
                "is_latest": True,
                "storage_class": "STANDARD",
            },
            {
                "version_id": "v2",
                "last_modified": _DT,
                "size": 200,
                "etag": "etag2",
                "is_latest": False,
                "storage_class": "GLACIER",
            },
        ]
        assert data["delete_markers"] == [
            {
                "version_id": "dm1",
                "last_modified": _DT,
                "is_latest": False,
            },
        ]
        mock_s3.list_object_versions.assert_called_once_with(
            Bucket=_BUCKET, Prefix=_KEY, MaxKeys=10
        )

    def test_no_versions(self, mock_s3: MagicMock) -> None:
        mock_s3.list_object_versions.return_value = {}

        result = s3_client.list_object_versions(_BUCKET, _KEY)

        assert result["success"] is True
        assert result["data"]["versions"] == []
        assert result["data"]["delete_markers"] == []
        assert result["data"]["version_count"] == 0

    def test_client_error(self, mock_s3: MagicMock) -> None:
        mock_s3.list_object_versions.side_effect = _client_error("NoSuchBucket")

        result = s3_client.list_object_versions(_BUCKET, _KEY)

        assert result["success"] is False
        assert "NoSuchBucket" in result["error"]


# ===================================================================
# compare_versions (lines 300-377)
# ===================================================================


class TestCompareVersions:
    """Coverage: identical / different-text / binary / ClientError."""

    def test_identical_text(self, mock_s3: MagicMock) -> None:
        content = b"same content"
        resp = {"Body": _body(content), "ContentLength": len(content), "ContentType": "text/plain"}
        mock_s3.get_object.side_effect = [resp, resp]

        result = s3_client.compare_versions(_BUCKET, _KEY, "v1", "v2", max_bytes=4096)

        assert result["success"] is True
        d = result["data"]
        assert d["are_identical"] is True
        assert d["is_text"] is True
        assert d["size_diff"] == 0
        assert d["version_1"]["sample"] == "same content"
        assert d["version_2"]["sample"] == "same content"
        assert mock_s3.get_object.call_count == 2
        mock_s3.get_object.assert_has_calls(
            [
                call(Bucket=_BUCKET, Key=_KEY, VersionId="v1", Range="bytes=0-4095"),
                call(Bucket=_BUCKET, Key=_KEY, VersionId="v2", Range="bytes=0-4095"),
            ]
        )

    def test_different_text(self, mock_s3: MagicMock) -> None:
        resp1 = {"Body": _body(b"version one"), "ContentLength": 11, "ContentType": "text/plain"}
        resp2 = {"Body": _body(b"version two"), "ContentLength": 11, "ContentType": "text/plain"}
        mock_s3.get_object.side_effect = [resp1, resp2]

        result = s3_client.compare_versions(_BUCKET, _KEY, "v1", "v2")

        assert result["success"] is True
        d = result["data"]
        assert d["are_identical"] is False
        assert d["is_text"] is True
        assert d["version_1"]["sample"] == "version one"
        assert d["version_2"]["sample"] == "version two"
        assert d["size_diff"] == 0

    def test_binary_content(self, mock_s3: MagicMock) -> None:
        raw = b"\x00\xff\xfe"
        resp1 = {"Body": _body(raw), "ContentLength": 3, "ContentType": "application/octet-stream"}
        resp2 = {"Body": _body(raw), "ContentLength": 3, "ContentType": "application/octet-stream"}
        mock_s3.get_object.side_effect = [resp1, resp2]

        result = s3_client.compare_versions(_BUCKET, _KEY, "v1", "v2")

        assert result["success"] is True
        d = result["data"]
        assert d["is_text"] is False
        assert d["version_1"]["sample"] is None
        assert d["version_2"]["sample"] is None
        assert d["are_identical"] is True

    def test_client_error(self, mock_s3: MagicMock) -> None:
        mock_s3.get_object.side_effect = _client_error("NoSuchVersion")

        result = s3_client.compare_versions(_BUCKET, _KEY, "v_bad", "v_bad2")

        assert result["success"] is False
        assert "NoSuchVersion" in result["error"]


# ===================================================================
# list_objects (lines 380-433)
# ===================================================================


class TestListObjects:
    """Coverage: objects-with-prefix / empty / ClientError."""

    def test_objects_returned(self, mock_s3: MagicMock) -> None:
        mock_s3.list_objects_v2.return_value = {
            "Contents": [
                {
                    "Key": "logs/2024/01/01/file1.json",
                    "Size": 512,
                    "LastModified": _DT,
                    "ETag": '"e1"',
                    "StorageClass": "STANDARD",
                },
                {
                    "Key": "logs/2024/01/01/file2.json",
                    "Size": 1024,
                    "LastModified": _DT,
                    "ETag": '"e2"',
                    "StorageClass": "GLACIER",
                },
            ],
            "IsTruncated": False,
        }

        result = s3_client.list_objects(_BUCKET, prefix="logs/2024/01/01/", max_keys=100)

        assert result["success"] is True
        data = result["data"]
        assert data["bucket"] == _BUCKET
        assert data["prefix"] == "logs/2024/01/01/"
        assert data["count"] == 2
        assert data["is_truncated"] is False
        assert data["objects"] == [
            {
                "key": "logs/2024/01/01/file1.json",
                "size": 512,
                "last_modified": _DT,
                "etag": "e1",
                "storage_class": "STANDARD",
            },
            {
                "key": "logs/2024/01/01/file2.json",
                "size": 1024,
                "last_modified": _DT,
                "etag": "e2",
                "storage_class": "GLACIER",
            },
        ]
        mock_s3.list_objects_v2.assert_called_once_with(
            Bucket=_BUCKET, Prefix="logs/2024/01/01/", MaxKeys=100
        )

    def test_no_objects(self, mock_s3: MagicMock) -> None:
        mock_s3.list_objects_v2.return_value = {"Contents": [], "IsTruncated": False}

        result = s3_client.list_objects(_BUCKET, prefix="nonexistent/")

        assert result["success"] is True
        data = result["data"]
        assert data["objects"] == []
        assert data["count"] == 0

    def test_client_error(self, mock_s3: MagicMock) -> None:
        mock_s3.list_objects_v2.side_effect = _client_error("NoSuchBucket")

        result = s3_client.list_objects(_BUCKET)

        assert result["success"] is False
        assert "NoSuchBucket" in result["error"]


# ===================================================================
# Edge cases
# ===================================================================


class TestBoto3NotAvailable:
    """make_boto3_client returns None -- all public functions bail early."""

    @pytest.fixture(autouse=True)
    def _no_boto3(self) -> Generator[None]:
        """Patch make_boto3_client to return None; do NOT patch credentials
        (the early-return should fire before credentials are checked)."""
        p = patch("app.services.s3_client.make_boto3_client", return_value=None)
        p.start()
        yield
        p.stop()

    @pytest.mark.parametrize(
        "func, kwargs",
        [
            (s3_client.head_object, {"bucket": "b", "key": "k"}),
            (s3_client.get_object_sample, {"bucket": "b", "key": "k"}),
            (s3_client.get_full_object, {"bucket": "b", "key": "k"}),
            (s3_client.list_object_versions, {"bucket": "b", "key": "k"}),
            (
                s3_client.compare_versions,
                {"bucket": "b", "key": "k", "version_id_1": "a", "version_id_2": "b"},
            ),
            (s3_client.list_objects, {"bucket": "b"}),
        ],
    )
    def test_returns_boto3_not_available(self, func, kwargs) -> None:
        result = func(**kwargs)
        assert result == {"success": False, "error": "boto3 not available"}


class TestGetObjectMetadata:
    """get_object_metadata is a direct alias for head_object."""

    def test_delegates_to_head_object(self, mock_s3: MagicMock) -> None:
        mock_s3.head_object.return_value = {
            "ContentLength": 42,
            "LastModified": _DT,
            "ContentType": "text/plain",
            "ETag": _ETAG_QUOTED,
            "VersionId": None,
            "StorageClass": "STANDARD",
            "Metadata": {},
        }

        result = s3_client.get_object_metadata(_BUCKET, _KEY)

        assert result["success"] is True
        assert result["exists"] is True
        assert result["data"]["size"] == 42
        mock_s3.head_object.assert_called_once_with(Bucket=_BUCKET, Key=_KEY)
