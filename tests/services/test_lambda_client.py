"""Unit tests for the Lambda service client."""

import base64
import json
from io import BytesIO
from unittest.mock import MagicMock, patch
from zipfile import ZipFile

import pytest
from botocore.exceptions import ClientError

from app.services.lambda_client import (
    get_function_code,
    get_function_configuration,
    get_invocation_logs_by_request_id,
    get_recent_invocations,
    invoke_function,
    list_functions,
)


@pytest.fixture(autouse=True)
def mock_aws_credentials():
    with patch("app.services.lambda_client.require_aws_credentials", return_value=None):
        yield


@pytest.fixture
def mock_lambda_client():
    with patch("app.services.lambda_client._get_lambda_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


@pytest.fixture
def mock_logs_client():
    with patch("app.services.lambda_client._get_cloudwatch_logs_client") as mock:
        client = MagicMock()
        mock.return_value = client
        yield client


def test_get_function_configuration_success(mock_lambda_client) -> None:
    mock_lambda_client.get_function_configuration.return_value = {
        "FunctionName": "test-func",
        "FunctionArn": "arn:aws:lambda:us-east-1:123:function:test-func",
        "Runtime": "python3.12",
        "Handler": "index.handler",
        "CodeSize": 1024,
        "Timeout": 30,
        "MemorySize": 128,
        "LastModified": "2024-01-01T00:00:00.000+0000",
        "Role": "arn:aws:iam::123:role/lambda-role",
        "Environment": {"Variables": {"KEY": "VALUE"}},
        "Description": "Test function",
        "Version": "$LATEST",
        "State": "Active",
        "Layers": [{"Arn": "arn:layer", "CodeSize": 500}],
    }

    result = get_function_configuration("test-func")

    assert result["success"] is True
    data = result["data"]
    assert data["function_name"] == "test-func"
    assert data["runtime"] == "python3.12"
    assert data["environment"] == {"KEY": "VALUE"}
    assert data["layers"][0]["arn"] == "arn:layer"


def test_get_function_configuration_error(mock_lambda_client) -> None:
    error_response = {"Error": {"Code": "ResourceNotFoundException", "Message": "Not found"}}
    mock_lambda_client.get_function_configuration.side_effect = ClientError(
        error_response, "GetFunctionConfiguration"
    )

    result = get_function_configuration("missing-func")

    assert result["success"] is False
    assert "ResourceNotFoundException" in result["error"]


def test_get_function_code_success(mock_lambda_client) -> None:
    mock_lambda_client.get_function.return_value = {
        "Code": {"Location": "https://example.com/zip", "RepositoryType": "S3"},
        "Configuration": {"CodeSize": 2048},
    }

    # Create a mock zip file
    zip_buffer = BytesIO()
    with ZipFile(zip_buffer, "w") as zf:
        zf.writestr("index.py", "def handler(): pass")
        zf.writestr("data.bin", b"\x80\x81\x82")  # Invalid UTF-8
        zf.writestr("large.txt", "a" * 100)  # Will be "large" relative to max_file_size

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = zip_buffer.getvalue()

    with patch("requests.get", return_value=mock_response):
        result = get_function_code("test-func", max_file_size=50)

    assert result["success"] is True
    files = result["data"]["files"]
    assert "index.py" in files
    assert files["index.py"]["content"] == "def handler(): pass"
    assert files["data.bin"]["binary"] is True
    assert files["large.txt"]["truncated"] is True
    assert result["data"]["file_count"] == 3


def test_get_recent_invocations_success(mock_logs_client) -> None:
    mock_logs_client.filter_log_events.return_value = {
        "events": [
            {"timestamp": 1000, "message": "START RequestId: req1 Version: $LATEST\n"},
            {"timestamp": 1100, "message": "hello world\n"},
            {"timestamp": 1200, "message": "END RequestId: req1\n"},
            {
                "timestamp": 1300,
                "message": "REPORT RequestId: req1\tDuration: 200.00 ms\tBilled Duration: 200 ms\tMemory Size: 128 MB\tMax Memory Used: 64 MB\t\n",
            },
        ]
    }

    result = get_recent_invocations("test-func")

    assert result["success"] is True
    assert result["data"]["invocation_count"] == 1
    invocation = result["data"]["invocations"][0]
    assert invocation["request_id"] == "req1"
    assert invocation["duration_ms"] == 200.0
    assert invocation["memory_used_mb"] == 64
    assert len(invocation["logs"]) == 4


def test_get_recent_invocations_resource_not_found(mock_logs_client) -> None:
    error_response = {
        "Error": {"Code": "ResourceNotFoundException", "Message": "Log group not found"}
    }
    mock_logs_client.filter_log_events.side_effect = ClientError(error_response, "FilterLogEvents")

    result = get_recent_invocations("missing-func")

    assert result["success"] is False
    assert "Log group not found" in result["error"]


def test_invoke_function_success(mock_lambda_client) -> None:
    mock_response = {
        "StatusCode": 200,
        "ExecutedVersion": "$LATEST",
        "Payload": BytesIO(json.dumps({"body": "ok"}).encode()),
        "LogResult": base64.b64encode(b"some logs").decode(),
    }
    mock_lambda_client.invoke.return_value = mock_response

    result = invoke_function("test-func", payload={"key": "val"})

    assert result["success"] is True
    assert result["data"]["payload"] == {"body": "ok"}
    assert result["data"]["log_result"] == "some logs"

    # Verify call
    mock_lambda_client.invoke.assert_called_once_with(
        FunctionName="test-func",
        InvocationType="RequestResponse",
        Payload=json.dumps({"key": "val"}),
    )


def test_get_function_code_no_extract(mock_lambda_client) -> None:
    mock_lambda_client.get_function.return_value = {
        "Code": {"Location": "https://example.com/zip", "RepositoryType": "S3"},
        "Configuration": {"CodeSize": 1024},
    }
    # Should not call requests.get
    with patch("requests.get") as mock_get:
        result = get_function_code("test-func", extract_files=False)
        assert result["success"] is True
        assert "files" not in result["data"]
        mock_get.assert_not_called()


def test_get_function_code_too_large(mock_lambda_client) -> None:
    mock_lambda_client.get_function.return_value = {
        "Code": {"Location": "https://example.com/zip", "RepositoryType": "S3"},
        "Configuration": {"CodeSize": 10 * 1024 * 1024},  # 10MB
    }
    with patch("requests.get") as mock_get:
        result = get_function_code("test-func")
        assert result["success"] is True
        assert "files" not in result["data"]
        mock_get.assert_not_called()


def test_get_invocation_logs_by_request_id_success(mock_logs_client) -> None:
    mock_logs_client.filter_log_events.return_value = {
        "events": [
            {"message": "log 1\n"},
            {"message": "log 2\n"},
        ]
    }

    result = get_invocation_logs_by_request_id("test-func", "req1")

    assert result["success"] is True
    assert result["data"]["event_count"] == 2
    assert result["data"]["logs"] == ["log 1\n", "log 2\n"]


def test_list_functions_success(mock_lambda_client) -> None:
    mock_lambda_client.list_functions.return_value = {
        "Functions": [
            {
                "FunctionName": "func1",
                "FunctionArn": "arn1",
                "Runtime": "python3.12",
                "Handler": "h1",
                "CodeSize": 100,
                "Timeout": 10,
                "MemorySize": 128,
                "LastModified": "date1",
            }
        ]
    }

    result = list_functions()

    assert result["success"] is True
    assert len(result["data"]["functions"]) == 1
    assert result["data"]["functions"][0]["function_name"] == "func1"


def test_get_function_code_download_error(mock_lambda_client) -> None:
    mock_lambda_client.get_function.return_value = {
        "Code": {"Location": "https://example.com/zip", "RepositoryType": "S3"},
        "Configuration": {"CodeSize": 1024},
    }
    mock_response = MagicMock()
    mock_response.status_code = 404
    with patch("requests.get", return_value=mock_response):
        result = get_function_code("test-func")

    assert result["success"] is True  # The get_function call succeeded
    assert "files" not in result["data"]


def test_get_function_code_corrupt_zip(mock_lambda_client) -> None:
    mock_lambda_client.get_function.return_value = {
        "Code": {"Location": "https://example.com/zip", "RepositoryType": "S3"},
        "Configuration": {"CodeSize": 1024},
    }
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.content = b"not a zip file"

    with patch("requests.get", return_value=mock_response):
        result = get_function_code("test-func")

    assert result["success"] is True
    assert "extract_error" in result["data"]


def test_get_recent_invocations_multiple(mock_logs_client) -> None:
    # Test grouping of multiple overlapping/sequential invocations
    mock_logs_client.filter_log_events.return_value = {
        "events": [
            {"timestamp": 1000, "message": "START RequestId: req1\n"},
            {"timestamp": 1050, "message": "log 1\n"},
            {"timestamp": 1100, "message": "END RequestId: req1\n"},
            {
                "timestamp": 1150,
                "message": "REPORT RequestId: req1\tDuration: 100.00 ms\tBilled Duration: 100 ms\tMemory Size: 128 MB\tMax Memory Used: 64 MB\t\n",
            },
            {"timestamp": 1200, "message": "START RequestId: req2\n"},
            {"timestamp": 1250, "message": "log 2\n"},
            {
                "timestamp": 1300,
                "message": "REPORT RequestId: req2\tDuration: 50.00 ms\tBilled Duration: 50 ms\tMemory Size: 128 MB\tMax Memory Used: 32 MB\t\n",
            },
        ]
    }

    result = get_recent_invocations("test-func")

    assert result["success"] is True
    assert result["data"]["invocation_count"] == 2
    inv1 = result["data"]["invocations"][0]
    inv2 = result["data"]["invocations"][1]
    assert inv1["request_id"] == "req1"
    assert inv1["duration_ms"] == 100.0
    assert inv2["request_id"] == "req2"
    assert inv2["duration_ms"] == 50.0
    assert len(inv1["logs"]) == 4
    assert len(inv2["logs"]) == 3
