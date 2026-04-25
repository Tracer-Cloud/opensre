"""Unit tests for the generic AWS SDK client."""

from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ParamValidationError

from app.services.aws_sdk_client import execute_aws_sdk_call


@pytest.fixture
def mock_boto3():
    with patch("boto3.client") as mock:
        yield mock


def test_execute_aws_sdk_call_success(mock_boto3):
    """Test successful AWS SDK call execution."""
    mock_client = MagicMock()
    mock_boto3.return_value = mock_client

    # Mock a describe_instances call
    mock_client.describe_instances.return_value = {
        "Reservations": [],
        "ResponseMetadata": {"HTTPStatusCode": 200}
    }
    mock_client.meta.region_name = "us-east-1"

    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="describe_instances",
        parameters={"InstanceIds": ["i-12345"]}
    )

    assert result["success"] is True
    assert result["service"] == "ec2"
    assert result["operation"] == "describe_instances"
    assert "Reservations" in result["data"]
    assert "ResponseMetadata" not in result["data"]  # Should be sanitized out
    assert result["metadata"]["region"] == "us-east-1"


def test_execute_aws_sdk_call_blocked_operation(mock_boto3):
    """Test that blocked (destructive) operations are rejected."""
    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="terminate_instances",
        parameters={"InstanceIds": ["i-12345"]}
    )

    assert result["success"] is False
    assert "Operation not allowed" in result["error"]
    assert result["metadata"]["validation_failed"] is True
    mock_boto3.assert_not_called()


def test_execute_aws_sdk_call_invalid_operation(mock_boto3):
    """Test that non-existent operations are handled."""
    mock_client = MagicMock()
    mock_boto3.return_value = mock_client

    # Remove the attribute to simulate non-existent operation
    del mock_client.non_existent_op

    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="non_existent_op"
    )

    assert result["success"] is False
    assert "not found in service" in result["error"]


def test_execute_aws_sdk_call_no_credentials(mock_boto3):
    """Test handling of missing AWS credentials."""
    mock_boto3.side_effect = NoCredentialsError()

    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="describe_instances"
    )

    assert result["success"] is False
    assert "credentials not configured" in result["error"]
    assert result["metadata"]["error_type"] == "credentials"


def test_execute_aws_sdk_call_param_validation_error(mock_boto3):
    """Test handling of parameter validation errors."""
    mock_client = MagicMock()
    mock_boto3.return_value = mock_client
    mock_client.describe_instances.side_effect = ParamValidationError(report="Invalid param")

    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="describe_instances",
        parameters={"InvalidParam": "value"}
    )

    assert result["success"] is False
    assert "Invalid parameters" in result["error"]
    assert result["metadata"]["error_type"] == "validation"


def test_execute_aws_sdk_call_client_error(mock_boto3):
    """Test handling of Boto3 ClientError."""
    mock_client = MagicMock()
    mock_boto3.return_value = mock_client

    error_response = {
        "Error": {
            "Code": "AccessDenied",
            "Message": "User is not authorized to perform: ec2:DescribeInstances"
        },
        "ResponseMetadata": {"HTTPStatusCode": 403}
    }
    mock_client.describe_instances.side_effect = ClientError(error_response, "DescribeInstances")

    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="describe_instances"
    )

    assert result["success"] is False
    assert "AccessDenied" in result["error"]
    assert result["metadata"]["error_type"] == "client_error"
    assert result["metadata"]["status_code"] == 403
