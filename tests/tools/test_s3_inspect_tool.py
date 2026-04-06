"""Tests for S3InspectTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.S3InspectTool import inspect_s3_object
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestS3InspectToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return inspect_s3_object.__opensre_registered_tool__


def test_is_available_requires_bucket_and_key() -> None:
    rt = inspect_s3_object.__opensre_registered_tool__
    assert rt.is_available({"s3": {"bucket": "b", "key": "k"}}) is True
    assert rt.is_available({"s3": {"bucket": "b"}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = inspect_s3_object.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["bucket"] == "my-bucket"
    assert params["key"] == "my-key.json"


def test_run_returns_error_when_no_bucket_or_key() -> None:
    result = inspect_s3_object(bucket="", key="")
    assert "error" in result


def test_run_not_found() -> None:
    with patch("app.tools.S3InspectTool.get_object_metadata", return_value={"success": True, "exists": False}):
        result = inspect_s3_object(bucket="b", key="k")
    assert result["found"] is False


def test_run_happy_path() -> None:
    fake_meta = {
        "size": 2048,
        "last_modified": "2024-01-01",
        "content_type": "text/plain",
        "etag": "abc123",
        "version_id": None,
        "metadata": {},
    }
    fake_sample = {"is_text": True, "sample": "hello world", "sample_bytes": 11}
    with patch("app.tools.S3InspectTool.get_object_metadata", return_value={"success": True, "exists": True, "data": fake_meta}), \
         patch("app.tools.S3InspectTool.get_object_sample", return_value={"success": True, "data": fake_sample}):
        result = inspect_s3_object(bucket="b", key="k")
    assert result["found"] is True
    assert result["size"] == 2048
    assert result["is_text"] is True


def test_run_metadata_error() -> None:
    with patch("app.tools.S3InspectTool.get_object_metadata", return_value={"success": False, "error": "Forbidden"}):
        result = inspect_s3_object(bucket="b", key="k")
    assert "error" in result
