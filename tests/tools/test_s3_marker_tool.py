"""Tests for S3MarkerTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.S3MarkerTool import check_s3_marker
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestS3MarkerToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return check_s3_marker.__opensre_registered_tool__


def test_is_available_requires_bucket_and_prefix() -> None:
    rt = check_s3_marker.__opensre_registered_tool__
    assert rt.is_available({"s3": {"bucket": "b", "prefix": "p/"}}) is True
    assert rt.is_available({"s3_processed": {"bucket": "b"}}) is True
    assert rt.is_available({"s3": {"bucket": "b"}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = check_s3_marker.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["bucket"] == "my-bucket"


def test_run_marker_exists() -> None:
    mock_result = MagicMock()
    mock_result.marker_exists = True
    mock_result.file_count = 5
    mock_result.files = ["_SUCCESS"]
    mock_s3_client = MagicMock()
    mock_s3_client.check_marker.return_value = mock_result
    with patch("app.tools.S3MarkerTool.get_s3_client", return_value=mock_s3_client):
        result = check_s3_marker(bucket="b", prefix="data/")
    assert result["marker_exists"] is True
    assert result["file_count"] == 5


def test_run_marker_missing() -> None:
    mock_result = MagicMock()
    mock_result.marker_exists = False
    mock_result.file_count = 0
    mock_result.files = []
    mock_s3_client = MagicMock()
    mock_s3_client.check_marker.return_value = mock_result
    with patch("app.tools.S3MarkerTool.get_s3_client", return_value=mock_s3_client):
        result = check_s3_marker(bucket="b", prefix="data/")
    assert result["marker_exists"] is False
