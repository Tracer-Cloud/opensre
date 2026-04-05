"""Tests for LambdaErrorsTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.LambdaErrorsTool import get_lambda_errors
from tests.tools.conftest import BaseToolContract, mock_agent_state


class TestLambdaErrorsToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return get_lambda_errors.__opensre_registered_tool__


def test_is_available_requires_function_name() -> None:
    rt = get_lambda_errors.__opensre_registered_tool__
    assert rt.is_available({"lambda": {"function_name": "my-fn"}}) is True
    assert rt.is_available({"lambda": {}}) is False
    assert rt.is_available({}) is False


def test_extract_params_maps_fields() -> None:
    rt = get_lambda_errors.__opensre_registered_tool__
    sources = mock_agent_state()
    params = rt.extract_params(sources)
    assert params["function_name"] == "my-lambda-function"


def test_run_delegates_to_invocation_logs_with_filter_errors() -> None:
    fake_data = {
        "log_group": "/aws/lambda/my-fn",
        "invocation_count": 1,
        "invocations": [],
    }
    with patch(
        "app.tools.LambdaInvocationLogsTool.get_recent_invocations",
        return_value={"success": True, "data": fake_data},
    ):
        result = get_lambda_errors(function_name="my-fn", limit=50)
    # Delegates to get_lambda_invocation_logs(filter_errors=True)
    assert "found" in result


def test_run_returns_error_when_no_function_name() -> None:
    result = get_lambda_errors(function_name="")
    assert "error" in result
