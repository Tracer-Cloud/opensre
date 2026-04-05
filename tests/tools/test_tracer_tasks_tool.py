"""Tests for TracerTasksTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from app.tools.TracerTasksTool import get_tracer_tasks
from tests.tools.conftest import BaseToolContract


class TestTracerTasksToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_tracer_tasks.__opensre_registered_tool__


def test_is_available_requires_tracer_web() -> None:
    rt = get_tracer_tasks.__opensre_registered_tool__
    assert rt.is_available({"tracer_web": {"some": "data"}}) is True
    assert rt.is_available({}) is False


def test_metadata() -> None:
    rt = get_tracer_tasks.__opensre_registered_tool__
    assert rt.name == "get_tracer_tasks"
    assert rt.source == "tracer_web"


def test_run_returns_task_result() -> None:
    mock_client = MagicMock()
    mock_result = MagicMock()
    mock_client.get_run_tasks.return_value = mock_result
    with patch("app.tools.TracerTasksTool.get_tracer_client", return_value=mock_client):
        result = get_tracer_tasks(run_id="run-123")
    assert result is mock_result
    mock_client.get_run_tasks.assert_called_once_with("run-123")
