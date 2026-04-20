"""Tests for S3ListTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.S3ListTool import list_s3_objects
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestS3ListToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return list_s3_objects.__opensre_registered_tool__


def test_is_available_requires_bucket() -> None:
    rt = list_s3_objects.__opensre_registered_tool__
    assert rt.is_available({"s3": {"bucket": "b"}}) is True
    assert rt.is_available({"s3": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = list_s3_objects.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["bucket"] == "my-bucket"
    assert params["prefix"] == "my-prefix/"


def test_run_returns_error_when_no_bucket() -> None:
    result = list_s3_objects(bucket="")
    assert "error" in result


def test_run_happy_path() -> None:
    fake_data = {
        "count": 3,
        "objects": [{"key": "a.csv"}, {"key": "b.csv"}, {"key": "c.csv"}],
        "is_truncated": False,
    }
    with patch(
        "app.tools.S3ListTool.list_objects", return_value={"success": True, "data": fake_data}
    ):
        result = list_s3_objects(bucket="my-bucket", prefix="data/")
    assert result["found"] is True
    assert result["count"] == 3
    assert len(result["objects"]) == 3


def test_run_empty_bucket() -> None:
    fake_data = {"count": 0, "objects": [], "is_truncated": False}
    with patch(
        "app.tools.S3ListTool.list_objects", return_value={"success": True, "data": fake_data}
    ):
        result = list_s3_objects(bucket="empty-bucket")
    assert result["found"] is False
    assert result["count"] == 0


def test_run_api_error() -> None:
    with patch(
        "app.tools.S3ListTool.list_objects",
        return_value={"success": False, "error": "No such bucket"},
    ):
        result = list_s3_objects(bucket="missing-bucket")
    assert "error" in result
