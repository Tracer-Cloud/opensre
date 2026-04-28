"""Tests for TracerToolsMixin."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.services.tracer_client.tracer_tools import TracerToolsMixin


class FakeTracerClient(TracerToolsMixin):
    """Fake subclass that stubs _get()."""

    def __init__(self) -> None:
        """Initialize with dummy values, avoiding side effects from base class."""
        with (
            patch(
                "app.services.tracer_client.tracer_client_base.extract_org_slug_from_jwt",
                return_value="test-slug",
            ),
            patch("httpx.Client"),
        ):
            super().__init__(
                base_url="https://api.tracer.cloud", org_id="test-org", jwt_token="dummy-jwt"
            )
        self._get_response: dict[str, Any] = {}

    def _get(self, _endpoint: str, _params: Any = None) -> dict[str, Any]:
        """Stub for the GET request."""
        return self._get_response


def test_get_run_tasks_all_success() -> None:
    """Test get_run_tasks when all tasks are successful."""
    client = FakeTracerClient()
    client._get_response = {
        "success": True,
        "data": [
            {"tool_name": "ls", "exit_code": "0", "runtime_ms": 100},
            {"tool_name": "pwd", "exit_code": None, "runtime_ms": 50},
            {"tool_name": "whoami", "exit_code": "", "runtime_ms": 75},
        ],
    }

    result = client.get_run_tasks("run-123")

    assert result.found is True
    assert result.total_tasks == 3
    assert result.failed_tasks == 0
    assert result.completed_tasks == 3
    assert len(result.tasks or []) == 3
    assert len(result.failed_task_details or []) == 0


def test_get_run_tasks_mixed_failure() -> None:
    """Test get_run_tasks with a mix of successful and failed tasks."""
    client = FakeTracerClient()
    client._get_response = {
        "success": True,
        "data": [
            {"tool_name": "ls", "exit_code": "0", "runtime_ms": 100},
            {
                "tool_name": "grep",
                "exit_code": "1",
                "runtime_ms": 200,
                "tool_cmd": "grep foo bar",
                "reason": "pattern not found",
                "explanation": "The file did not contain 'foo'",
            },
        ],
    }

    result = client.get_run_tasks("run-123")

    assert result.found is True
    assert result.total_tasks == 2
    assert result.failed_tasks == 1
    assert result.completed_tasks == 1

    assert result.failed_task_details is not None
    failed = result.failed_task_details[0]
    assert failed["tool_name"] == "grep"
    assert failed["exit_code"] == "1"
    assert failed["tool_cmd"] == "grep foo bar"
    assert failed["reason"] == "pattern not found"
    assert failed["explanation"] == "The file did not contain 'foo'"


def test_get_run_tasks_empty_payload() -> None:
    """Test get_run_tasks with an empty data payload."""
    client = FakeTracerClient()
    client._get_response = {"success": True, "data": []}

    result = client.get_run_tasks("run-123")

    assert result.found is False


def test_get_run_tasks_unsuccessful_response() -> None:
    """Test get_run_tasks with an unsuccessful API response."""
    client = FakeTracerClient()
    client._get_response = {"success": False, "error": "Not Found"}

    result = client.get_run_tasks("run-123")
    assert result.found is False


@pytest.mark.parametrize(
    ("code", "expected_fail"),
    [
        ("0", False),
        ("", False),
        (None, False),
        ("1", True),
        ("127", True),
        (2, True),
    ],
)
def test_get_run_tasks_exit_code_handling(code: Any, expected_fail: bool) -> None:
    """Test get_run_tasks with various exit code formats."""
    client = FakeTracerClient()
    client._get_response = {"success": True, "data": [{"tool_name": "test", "exit_code": code}]}
    result = client.get_run_tasks("run-123")
    assert (result.failed_tasks == 1) is expected_fail
