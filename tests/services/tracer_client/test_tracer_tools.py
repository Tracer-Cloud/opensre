"""Tests for TracerToolsMixin."""

from typing import Any

from app.services.tracer_client.tracer_tools import TracerTaskResult, TracerToolsMixin


class FakeTracerClient(TracerToolsMixin):
    """Fake subclass that stubs _get()."""

    def __init__(self):
        # We don't need real args for the mixin tests
        self.base_url = "https://api.tracer.cloud"
        self.org_id = "test-org"
        self._get_response = {}

    def _get(self, _endpoint: str, _params: Any = None) -> dict[str, Any]:
        return self._get_response


def test_get_run_tasks_all_success():
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
    assert len(result.tasks) == 3
    assert len(result.failed_task_details) == 0


def test_get_run_tasks_mixed_failure():
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

    failed = result.failed_task_details[0]
    assert failed["tool_name"] == "grep"
    assert failed["exit_code"] == "1"
    assert failed["tool_cmd"] == "grep foo bar"
    assert failed["reason"] == "pattern not found"
    assert failed["explanation"] == "The file did not contain 'foo'"


def test_get_run_tasks_empty_payload():
    client = FakeTracerClient()
    client._get_response = {"success": True, "data": []}

    result = client.get_run_tasks("run-123")

    assert result.found is False  # data.get("data") is truthy check in code?
    # Wait, if not data.get("data"): return found=False
    # An empty list is falsy in Python.


def test_get_run_tasks_unsuccessful_response():
    client = FakeTracerClient()
    client._get_response = {"success": False, "error": "Not Found"}

    result = client.get_run_tasks("run-123")
    assert result.found is False


def test_get_run_tasks_exit_code_handling():
    client = FakeTracerClient()

    cases = [
        ("0", False),
        ("", False),
        (None, False),
        ("1", True),
        ("127", True),
        (2, True),  # The code checks 'exit_code not in ("0", "", None)'
    ]

    for code, expected_fail in cases:
        client._get_response = {"success": True, "data": [{"tool_name": "test", "exit_code": code}]}
        result = client.get_run_tasks("run-123")
        assert (result.failed_tasks == 1) is expected_fail, f"Failed for exit_code: {code}"
