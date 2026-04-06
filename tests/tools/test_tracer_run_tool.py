"""Tests for TracerRunTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.TracerRunTool import get_tracer_run
from tests.tools.conftest import BaseToolContract


class TestTracerRunToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_tracer_run.__opensre_registered_tool__


def test_is_available_requires_tracer_web() -> None:
    rt = get_tracer_run.__opensre_registered_tool__
    assert rt.is_available({"tracer_web": {"some": "data"}}) is True
    assert rt.is_available({}) is False


def test_metadata() -> None:
    rt = get_tracer_run.__opensre_registered_tool__
    assert rt.name == "get_tracer_run"
    assert rt.source == "tracer_web"


def test_run_returns_run_result() -> None:
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_client.get_latest_run.return_value = mock_result
    with patch("app.tools.TracerRunTool.get_tracer_client", return_value=mock_client):
        result = get_tracer_run()
    assert result is mock_result


def test_run_with_pipeline_name() -> None:
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_client.get_latest_run.return_value = mock_result
    with patch("app.tools.TracerRunTool.get_tracer_client", return_value=mock_client):
        get_tracer_run(pipeline_name="my-pipeline")
    mock_client.get_latest_run.assert_called_once_with("my-pipeline")
