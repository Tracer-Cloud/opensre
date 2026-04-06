"""Tests for AWSOperationTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from app.tools.AWSOperationTool import execute_aws_operation
from tests.tools.conftest import BaseToolContract


class TestAWSOperationToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return execute_aws_operation.__opensre_registered_tool__


def test_is_available_never_auto_available() -> None:
    # This tool deliberately never auto-selects
    rt = execute_aws_operation.__opensre_registered_tool__
    assert rt.is_available({"aws_sdk": {"configured": True}}) is False
    assert rt.is_available({}) is False


def test_run_returns_error_when_no_service() -> None:
    result = execute_aws_operation(service="", operation="describe_instances")
    assert result["found"] is False
    assert "error" in result


def test_run_returns_error_when_no_operation() -> None:
    result = execute_aws_operation(service="ec2", operation="")
    assert result["found"] is False
    assert "error" in result


def test_run_happy_path() -> None:
    fake_result = {
        "success": True,
        "data": {"Reservations": [{"Instances": [{"InstanceId": "i-1234"}]}]},
        "metadata": {"service": "ec2"},
    }
    with patch("app.tools.AWSOperationTool.execute_aws_sdk_call", return_value=fake_result):
        result = execute_aws_operation(
            service="ec2",
            operation="describe_instances",
            parameters={"Filters": [{"Name": "instance-state-name", "Values": ["running"]}]},
        )
    assert result["found"] is True
    assert result["service"] == "ec2"
    assert result["operation"] == "describe_instances"


def test_run_api_error() -> None:
    fake_result = {
        "success": False,
        "error": "NoCredentialsError",
        "metadata": {},
    }
    with patch("app.tools.AWSOperationTool.execute_aws_sdk_call", return_value=fake_result):
        result = execute_aws_operation(service="ec2", operation="describe_instances")
    assert result["found"] is False
    assert "error" in result


def test_metadata() -> None:
    rt = execute_aws_operation.__opensre_registered_tool__
    assert rt.name == "execute_aws_operation"
    assert rt.source == "aws_sdk"
