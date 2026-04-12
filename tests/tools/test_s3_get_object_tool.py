"""Tests for S3GetObjectTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.S3GetObjectTool import get_s3_object
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestS3GetObjectToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_s3_object.__opensre_registered_tool__


def test_is_available_requires_bucket_and_key() -> None:
    rt = get_s3_object.__opensre_registered_tool__
    assert rt.is_available({"s3": {"bucket": "b", "key": "k"}}) is True
    assert rt.is_available({"s3_audit": {"bucket": "b", "key": "k"}}) is True
    assert rt.is_available({"s3": {"bucket": "b"}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_s3_object.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["bucket"] == "my-bucket"
    assert params["key"] == "my-key.json"


def test_run_returns_error_when_no_bucket_or_key() -> None:
    result = get_s3_object(bucket="", key="")
    assert "error" in result


def test_run_happy_path() -> None:
    fake_data = {
        "size": 1024,
        "content_type": "application/json",
        "is_text": True,
        "content": '{"key": "value"}',
        "metadata": {},
    }
    with patch(
        "app.tools.S3GetObjectTool.get_full_object",
        return_value={"success": True, "data": fake_data},
    ):
        result = get_s3_object(bucket="my-bucket", key="my-key.json")
    assert result["found"] is True
    assert result["size"] == 1024


def test_run_not_found() -> None:
    with patch(
        "app.tools.S3GetObjectTool.get_full_object", return_value={"success": True, "exists": False}
    ):
        result = get_s3_object(bucket="b", key="k")
    assert result["found"] is False


def test_run_api_error() -> None:
    with patch(
        "app.tools.S3GetObjectTool.get_full_object",
        return_value={"success": False, "error": "Access denied"},
    ):
        result = get_s3_object(bucket="b", key="k")
    assert "error" in result
