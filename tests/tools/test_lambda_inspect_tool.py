"""Tests for LambdaInspectTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.LambdaInspectTool import inspect_lambda_function
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestLambdaInspectToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return inspect_lambda_function.__opensre_registered_tool__


def test_is_available_requires_function_name() -> None:
    rt = inspect_lambda_function.__opensre_registered_tool__
    assert rt.is_available({"lambda": {"function_name": "my-fn"}}) is True
    assert rt.is_available({"lambda": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = inspect_lambda_function.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["function_name"] == "my-lambda-function"
    assert params["include_code"] is True


def test_run_returns_error_when_no_function_name() -> None:
    result = inspect_lambda_function(function_name="")
    assert "error" in result


def test_run_happy_path_no_code() -> None:
    fake_config = {
        "function_name": "my-fn",
        "function_arn": "arn:aws:lambda:us-east-1:123:function:my-fn",
        "runtime": "python3.12",
        "handler": "handler.main",
        "timeout": 300,
        "memory_size": 1024,
        "code_size": 2048,
        "last_modified": "2024-01-01",
        "state": "Active",
        "environment": {},
        "description": "My function",
        "layers": [],
    }
    with patch(
        "app.tools.LambdaInspectTool.get_function_configuration",
        return_value={"success": True, "data": fake_config},
    ):
        result = inspect_lambda_function(function_name="my-fn", include_code=False)
    assert result["found"] is True
    assert result["runtime"] == "python3.12"
    assert "code" not in result


def test_run_happy_path_with_code() -> None:
    fake_config = {
        "function_name": "my-fn",
        "function_arn": "arn:aws:lambda:us-east-1:123:function:my-fn",
        "runtime": "python3.12",
        "handler": "handler.main",
        "timeout": 300,
        "memory_size": 1024,
        "code_size": 2048,
        "last_modified": "2024-01-01",
        "state": "Active",
        "environment": {},
        "description": "My function",
        "layers": [],
    }
    fake_code = {"file_count": 2, "files": {"handler.py": "def main(): pass"}}
    with (
        patch(
            "app.tools.LambdaInspectTool.get_function_configuration",
            return_value={"success": True, "data": fake_config},
        ),
        patch(
            "app.tools.LambdaInspectTool.get_function_code",
            return_value={"success": True, "data": fake_code},
        ),
    ):
        result = inspect_lambda_function(function_name="my-fn", include_code=True)
    assert result["found"] is True
    assert result["code"]["file_count"] == 2


def test_run_config_error() -> None:
    with patch(
        "app.tools.LambdaInspectTool.get_function_configuration",
        return_value={"success": False, "error": "Not found"},
    ):
        result = inspect_lambda_function(function_name="unknown-fn")
    assert "error" in result
