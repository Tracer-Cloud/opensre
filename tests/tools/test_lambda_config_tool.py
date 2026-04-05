"""Tests for LambdaConfigTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.LambdaConfigTool import get_lambda_configuration
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestLambdaConfigToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_lambda_configuration.__opensre_registered_tool__


def test_is_available_requires_function_name() -> None:
    rt = get_lambda_configuration.__opensre_registered_tool__
    assert rt.is_available({"lambda": {"function_name": "my-fn"}}) is True
    assert rt.is_available({"lambda": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_lambda_configuration.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["function_name"] == "my-lambda-function"


def test_run_returns_error_when_no_function_name() -> None:
    result = get_lambda_configuration(function_name="")
    assert "error" in result


def test_run_happy_path() -> None:
    fake_data = {
        "function_name": "my-fn",
        "runtime": "python3.12",
        "handler": "handler.main",
        "timeout": 300,
        "memory_size": 1024,
        "last_modified": "2024-01-01",
        "state": "Active",
        "environment": {"KEY": "VALUE"},
    }
    with patch(
        "app.tools.LambdaConfigTool.get_function_configuration",
        return_value={"success": True, "data": fake_data},
    ):
        result = get_lambda_configuration(function_name="my-fn")
    assert result["found"] is True
    assert result["runtime"] == "python3.12"
    assert result["timeout"] == 300
    assert result["environment_variables"] == {"KEY": "VALUE"}


def test_run_api_error() -> None:
    with patch(
        "app.tools.LambdaConfigTool.get_function_configuration",
        return_value={"success": False, "error": "Function not found"},
    ):
        result = get_lambda_configuration(function_name="missing-fn")
    assert "error" in result
