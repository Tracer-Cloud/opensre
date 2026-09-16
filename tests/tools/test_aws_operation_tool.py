"""Tests for AWSOperationTool (function-based, @tool decorated)."""

from __future__ import annotations

from unittest.mock import patch

from integrations.aws.tools.aws_operation_tool import execute_aws_operation
from tests.tools.conftest import BaseToolContract

rt = execute_aws_operation.__opensre_registered_tool__

class TestAWSOperationToolContract(BaseToolContract):
    def get_tool_under_test(self):
        return rt


def test_is_available_with_verified_aws_connection():
    assert rt.is_available({"aws": {"connection_verified": True}}) is True


def test_is_available_with_role_arn():
    assert rt.is_available({"aws": {"role_arn": "arn:aws:iam::123456789012:role/test"}}) is True


def test_is_available_with_credentials():
    assert rt.is_available({"aws": {"credentials": {"access_key": "test"}}}) is True


def test_is_available_with_backend():
    assert rt.is_available({"aws": {"ec2_backend": object()}}) is True


def test_is_not_available_without_aws_source():
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
    with patch(
        "integrations.aws.tools.aws_operation_tool.execute_aws_sdk_call", return_value=fake_result
    ):
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
    with patch(
        "integrations.aws.tools.aws_operation_tool.execute_aws_sdk_call", return_value=fake_result
    ):
        result = execute_aws_operation(service="ec2", operation="describe_instances")
    assert result["found"] is False
    assert "error" in result


def test_metadata() -> None:
    rt = execute_aws_operation.__opensre_registered_tool__
    assert rt.name == "execute_aws_operation"
    assert rt.source == "aws_sdk"
