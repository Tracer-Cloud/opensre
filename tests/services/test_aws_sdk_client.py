import pytest
from datetime import datetime
import botocore.exceptions

import sys
import os
from app.services.aws_sdk_client import (
    _is_operation_allowed,
    _sanitize_response,
    execute_aws_sdk_call,
    MAX_LIST_ITEMS,
)
    execute_aws_sdk_call,
    MAX_LIST_ITEMS,
)


# ----------------------------
# 1. _is_operation_allowed()
# ----------------------------

def test_allowed_operation():
    allowed, reason = _is_operation_allowed("describe_instances")
    assert allowed is True
    assert "allowed" in reason.lower()


def test_blocked_operation():
    allowed, reason = _is_operation_allowed("delete_bucket")
    assert allowed is False
    assert "blocked pattern" in reason.lower()


def test_not_in_allowlist():
    allowed, reason = _is_operation_allowed("random_operation")
    assert allowed is False
    assert "does not match any allowed" in reason.lower()


# ----------------------------
# 2. _sanitize_response()
# ----------------------------

def test_sanitize_datetime():
    data = {"time": datetime(2024, 1, 1)}
    result = _sanitize_response(data)

    assert isinstance(result["time"], str)
    assert "2024" in result["time"]


def test_sanitize_bytes():
    data = {"file": b"hello"}
    result = _sanitize_response(data)

    assert "<binary data:" in result["file"]


def test_remove_response_metadata():
    data = {"a": 1, "ResponseMetadata": {"status": 200}}
    result = _sanitize_response(data)

    assert "ResponseMetadata" not in result


def test_deep_nesting_limit():
    data = current = {}
    for _ in range(12):  # deeper than max_depth=10
        current["x"] = {}
        current = current["x"]

    result = _sanitize_response(data)

    # Should hit depth limit
    def contains_truncation(d):
        if isinstance(d, dict):
            return any(contains_truncation(v) for v in d.values())
        return d == "... (max depth reached)"

    assert contains_truncation(result)


def test_list_truncation():
    data = {"items": list(range(200))}
    result = _sanitize_response(data)

    assert len(result["items"]) == MAX_LIST_ITEMS + 1
    assert "truncated" in result["items"][-1]


def test_tuple_handling():
    data = {"items": tuple(range(5))}
    result = _sanitize_response(data)

    assert isinstance(result["items"], list)


# ----------------------------
# 3. execute_aws_sdk_call()
# ----------------------------

class FakeClient:
    def __init__(self):
        self.meta = type("Meta", (), {"region_name": "us-east-1"})()

    def describe_instances(self):
        return {"Reservations": []}


def test_execute_success(monkeypatch):

    def mock_client(service_name, **kwargs):
        return FakeClient()

    monkeypatch.setattr("boto3.client", mock_client)

    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="describe_instances",
        parameters=None,
    )

    assert result["success"] is True
    assert result["data"] is not None
    assert result["error"] is None


def test_operation_not_allowed():
    result = execute_aws_sdk_call(
        service_name="s3",
        operation_name="delete_bucket",
    )

    assert result["success"] is False
    assert "not allowed" in result["error"].lower()
    assert result["metadata"]["validation_failed"] is True


def test_missing_operation(monkeypatch):

    class FakeClient:
        def __init__(self):
            self.meta = type("Meta", (), {"region_name": "us-east-1"})()

    def mock_client(service_name, **kwargs):
        return FakeClient()

    monkeypatch.setattr("boto3.client", mock_client)

    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="describe_something_fake",
    )

    assert result["success"] is False
    assert "not found" in result["error"].lower()


def test_credentials_error(monkeypatch):

    def mock_client(service_name, **kwargs):
        raise botocore.exceptions.NoCredentialsError()

    monkeypatch.setattr("boto3.client", mock_client)

    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="describe_instances",
    )

    assert result["success"] is False
    assert result["metadata"]["error_type"] == "credentials"


def test_param_validation_error(monkeypatch):

    def mock_client(service_name, **kwargs):
        class Fake:
            def __init__(self):
                self.meta = type("Meta", (), {"region_name": "us-east-1"})()

            def describe_instances(self, **kwargs):
                raise botocore.exceptions.ParamValidationError(report="bad params")

        return Fake()

    monkeypatch.setattr("boto3.client", mock_client)

    result = execute_aws_sdk_call(
        service_name="ec2",
        operation_name="describe_instances",
        parameters={"invalid": "param"},
    )

    assert result["success"] is False
    assert result["metadata"]["error_type"] == "validation"