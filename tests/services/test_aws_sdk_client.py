"""Direct unit tests for app/services/aws_sdk_client.py.

Covers:
- _is_operation_allowed(): allowlist, blocklist, and unknown operations
- _sanitize_response(): datetime, bytes, deep nesting, oversized lists,
  ResponseMetadata stripping, None, primitives
- execute_aws_sdk_call(): fake boto3 client, missing-op, credentials error,
  blocked operation, missing service/operation params

All tests are fully offline — no real AWS calls are made.

See: https://github.com/Tracer-Cloud/opensre/issues/884
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.services.aws_sdk_client import (
    MAX_LIST_ITEMS,
    _is_operation_allowed,
    _sanitize_response,
    execute_aws_sdk_call,
)


# ── _is_operation_allowed ─────────────────────────────────────────────────────


class TestIsOperationAllowed:
    """Allowlist / blocklist / default-deny behaviour."""

    @pytest.mark.parametrize(
        "op",
        [
            "describe_instances",
            "get_object",
            "list_buckets",
            "head_object",
            "query",
            "scan",
            "select_object_content",
            "batch_get_item",
            "lookup_events",
        ],
    )
    def test_allowed_operations_pass(self, op: str) -> None:
        allowed, reason = _is_operation_allowed(op)
        assert allowed, f"Expected '{op}' to be allowed; got: {reason}"

    @pytest.mark.parametrize(
        "op",
        [
            "delete_object",
            "remove_targets",
            "update_item",
            "put_object",
            "create_bucket",
            "modify_db_instance",
            "terminate_instances",
            "stop_instances",
            "start_instances",
            "reboot_instances",
            "attach_volume",
            "detach_volume",
            "associate_route_table",
            "disassociate_address",
        ],
    )
    def test_blocked_operations_are_rejected(self, op: str) -> None:
        allowed, reason = _is_operation_allowed(op)
        assert not allowed, f"Expected '{op}' to be blocked; got allowed=True"
        assert "blocked pattern" in reason

    def test_unknown_operation_is_denied(self) -> None:
        allowed, reason = _is_operation_allowed("frobnicate_everything")
        assert not allowed
        assert "does not match any allowed patterns" in reason

    def test_blocklist_takes_priority_over_allowlist(self) -> None:
        # "describe_delete" starts with describe_ (allowlist) but contains
        # "delete" — blocklist must win.
        allowed, _ = _is_operation_allowed("describe_delete_markers")
        assert not allowed

    def test_case_insensitive_matching(self) -> None:
        allowed, _ = _is_operation_allowed("DELETE_OBJECT")
        assert not allowed


# ── _sanitize_response ────────────────────────────────────────────────────────


class TestSanitizeResponse:
    """Response sanitization for safe downstream consumption."""

    def test_none_returns_none(self) -> None:
        assert _sanitize_response(None) is None

    def test_primitive_passthrough(self) -> None:
        assert _sanitize_response(42) == 42
        assert _sanitize_response("hello") == "hello"
        assert _sanitize_response(3.14) == 3.14
        assert _sanitize_response(True) is True

    def test_datetime_converted_to_iso_string(self) -> None:
        dt = datetime.datetime(2024, 1, 15, 12, 0, 0, tzinfo=datetime.timezone.utc)
        result = _sanitize_response(dt)
        assert isinstance(result, str)
        assert "2024-01-15" in result

    def test_bytes_converted_to_placeholder(self) -> None:
        result = _sanitize_response(b"\x00\x01\x02")
        assert isinstance(result, str)
        assert "binary data" in result
        assert "3 bytes" in result

    def test_response_metadata_stripped(self) -> None:
        data = {
            "Instances": [{"InstanceId": "i-123"}],
            "ResponseMetadata": {"RequestId": "abc", "HTTPStatusCode": 200},
        }
        result = _sanitize_response(data)
        assert "ResponseMetadata" not in result
        assert "Instances" in result

    def test_list_truncated_at_max_items(self) -> None:
        big_list = list(range(MAX_LIST_ITEMS + 50))
        result = _sanitize_response(big_list)
        # One extra element is appended as truncation notice
        assert len(result) == MAX_LIST_ITEMS + 1
        assert "truncated" in str(result[-1])

    def test_list_under_max_is_not_truncated(self) -> None:
        small_list = list(range(10))
        result = _sanitize_response(small_list)
        assert result == small_list

    def test_max_depth_protection(self) -> None:
        # Build deeply nested dict (12 levels)
        nested: dict = {}
        cursor = nested
        for _ in range(12):
            cursor["x"] = {}
            cursor = cursor["x"]
        result = _sanitize_response(nested)
        # Should not raise; deepest value replaced by sentinel string
        assert result is not None

    def test_nested_datetime_in_dict(self) -> None:
        dt = datetime.datetime(2023, 6, 1, tzinfo=datetime.timezone.utc)
        data = {"LaunchTime": dt, "State": "running"}
        result = _sanitize_response(data)
        assert isinstance(result["LaunchTime"], str)
        assert result["State"] == "running"

    def test_tuple_treated_like_list(self) -> None:
        result = _sanitize_response((1, 2, 3))
        assert result == [1, 2, 3]


# ── execute_aws_sdk_call ──────────────────────────────────────────────────────


def _make_fake_client(operation_name: str, return_value: dict) -> MagicMock:
    """Return a fake boto3 client that exposes *operation_name* as a method."""
    client = MagicMock()
    # hasattr check inside execute_aws_sdk_call uses getattr/hasattr on client
    setattr(client, operation_name, MagicMock(return_value=return_value))
    client.meta.region_name = "us-east-1"
    return client


class TestExecuteAwsSdkCall:
    """Integration-style tests for the public entry point."""

    def test_missing_service_name_returns_error(self) -> None:
        result = execute_aws_sdk_call("", "describe_instances")
        assert result["success"] is False
        assert "required" in result["error"]

    def test_missing_operation_name_returns_error(self) -> None:
        result = execute_aws_sdk_call("ec2", "")
        assert result["success"] is False

    def test_blocked_operation_returns_error_without_boto3_call(self) -> None:
        with patch("app.services.aws_sdk_client.boto3.client") as mock_boto:
            result = execute_aws_sdk_call("ec2", "delete_security_group")
        mock_boto.assert_not_called()
        assert result["success"] is False
        assert "not allowed" in result["error"]

    def test_successful_call_with_fake_client(self) -> None:
        fake_response = {"Reservations": [{"InstanceId": "i-abc"}]}
        fake_client = _make_fake_client("describe_instances", fake_response)
        with patch("app.services.aws_sdk_client.boto3.client", return_value=fake_client):
            result = execute_aws_sdk_call("ec2", "describe_instances")
        assert result["success"] is True
        assert result["data"] is not None
        assert result["error"] is None

    def test_parameters_forwarded_to_operation(self) -> None:
        op_mock = MagicMock(return_value={"Reservations": []})
        fake_client = MagicMock()
        fake_client.describe_instances = op_mock
        fake_client.meta.region_name = "eu-west-1"
        with patch("app.services.aws_sdk_client.boto3.client", return_value=fake_client):
            execute_aws_sdk_call(
                "ec2",
                "describe_instances",
                parameters={"Filters": [{"Name": "instance-state-name", "Values": ["running"]}]},
            )
        op_mock.assert_called_once_with(
            Filters=[{"Name": "instance-state-name", "Values": ["running"]}]
        )

    def test_missing_operation_on_client_returns_error(self) -> None:
        fake_client = MagicMock(spec=[])  # spec=[] means no attributes
        fake_client.meta = SimpleNamespace(region_name="us-east-1")
        with patch("app.services.aws_sdk_client.boto3.client", return_value=fake_client):
            result = execute_aws_sdk_call("ec2", "describe_instances")
        assert result["success"] is False
        assert "not found" in result["error"]

    def test_no_credentials_error_is_handled(self) -> None:
        from botocore.exceptions import NoCredentialsError

        with patch(
            "app.services.aws_sdk_client.boto3.client",
            side_effect=NoCredentialsError(),
        ):
            result = execute_aws_sdk_call("ec2", "describe_instances")
        assert result["success"] is False
        assert "credentials" in result["error"].lower()
        assert result["metadata"]["error_type"] == "credentials"

    def test_region_is_passed_to_boto3(self) -> None:
        fake_client = _make_fake_client("list_buckets", {"Buckets": []})
        with patch("app.services.aws_sdk_client.boto3.client", return_value=fake_client) as mock_boto:
            execute_aws_sdk_call("s3", "list_buckets", region="ap-southeast-1")
        mock_boto.assert_called_once_with("s3", region_name="ap-southeast-1")
