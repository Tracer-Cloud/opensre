"""Tests for LambdaInvocationLogsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.LambdaInvocationLogsTool import get_lambda_invocation_logs
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestLambdaInvocationLogsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_lambda_invocation_logs.__opensre_registered_tool__


def test_is_available_requires_function_name() -> None:
    rt = get_lambda_invocation_logs.__opensre_registered_tool__
    assert rt.is_available({"lambda": {"function_name": "my-fn"}}) is True
    assert rt.is_available({"lambda": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_lambda_invocation_logs.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["function_name"] == "my-lambda-function"


def test_run_returns_error_when_no_function_name() -> None:
    result = get_lambda_invocation_logs(function_name="")
    assert "error" in result


def test_run_by_request_id_happy_path() -> None:
    fake_data = {
        "log_group": "/aws/lambda/my-fn",
        "event_count": 5,
        "logs": ["START", "END"],
    }
    with patch(
        "app.tools.LambdaInvocationLogsTool.get_invocation_logs_by_request_id",
        return_value={"success": True, "data": fake_data},
    ):
        result = get_lambda_invocation_logs(function_name="my-fn", request_id="req-123")
    assert result["found"] is True
    assert result["log_group"] == "/aws/lambda/my-fn"
    assert result["event_count"] == 5


def test_run_by_request_id_error() -> None:
    with patch(
        "app.tools.LambdaInvocationLogsTool.get_invocation_logs_by_request_id",
        return_value={"success": False, "error": "Not found"},
    ):
        result = get_lambda_invocation_logs(function_name="my-fn", request_id="req-999")
    assert "error" in result


def test_run_recent_invocations_happy_path() -> None:
    fake_data = {
        "log_group": "/aws/lambda/my-fn",
        "invocation_count": 2,
        "invocations": [
            {"request_id": "r1", "duration_ms": 100, "memory_used_mb": 128, "logs": ["line1"]},
            {"request_id": "r2", "duration_ms": 200, "memory_used_mb": 256, "logs": []},
        ],
    }
    with patch(
        "app.tools.LambdaInvocationLogsTool.get_recent_invocations",
        return_value={"success": True, "data": fake_data},
    ):
        result = get_lambda_invocation_logs(function_name="my-fn")
    assert result["found"] is True
    assert result["invocation_count"] == 2
    assert len(result["invocations"]) == 2


def test_run_recent_invocations_error() -> None:
    with patch(
        "app.tools.LambdaInvocationLogsTool.get_recent_invocations",
        return_value={"success": False, "error": "Permission denied"},
    ):
        result = get_lambda_invocation_logs(function_name="my-fn")
    assert "error" in result
