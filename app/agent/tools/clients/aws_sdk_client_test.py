"""
Tests for AWS SDK client.

Integration tests - no mocks, real AWS API calls with read-only operations.
"""


from app.agent.tools.clients.aws_sdk_client import (
    _is_operation_allowed,
    _sanitize_response,
    execute_aws_sdk_call,
)


class TestOperationValidation:
    """Test operation allowlist/blocklist validation."""

    def test_allowed_describe_operations(self):
        is_allowed, _ = _is_operation_allowed("describe_instances")
        assert is_allowed

        is_allowed, _ = _is_operation_allowed("describe_tasks")
        assert is_allowed

    def test_allowed_get_operations(self):
        is_allowed, _ = _is_operation_allowed("get_object")
        assert is_allowed

        is_allowed, _ = _is_operation_allowed("get_role")
        assert is_allowed

    def test_allowed_list_operations(self):
        is_allowed, _ = _is_operation_allowed("list_buckets")
        assert is_allowed

        is_allowed, _ = _is_operation_allowed("list_functions")
        assert is_allowed

    def test_blocked_delete_operations(self):
        is_allowed, reason = _is_operation_allowed("delete_object")
        assert not is_allowed
        assert "blocked" in reason.lower()

    def test_blocked_update_operations(self):
        is_allowed, reason = _is_operation_allowed("update_function")
        assert not is_allowed
        assert "blocked" in reason.lower()

    def test_blocked_create_operations(self):
        is_allowed, reason = _is_operation_allowed("create_bucket")
        assert not is_allowed
        assert "blocked" in reason.lower()

    def test_blocked_put_operations(self):
        is_allowed, reason = _is_operation_allowed("put_object")
        assert not is_allowed
        assert "blocked" in reason.lower()

    def test_blocked_terminate_operations(self):
        is_allowed, reason = _is_operation_allowed("terminate_instances")
        assert not is_allowed
        assert "blocked" in reason.lower()

    def test_unknown_operation(self):
        is_allowed, reason = _is_operation_allowed("random_operation")
        assert not is_allowed
        assert "does not match" in reason.lower()


class TestResponseSanitization:
    """Test response sanitization logic."""

    def test_sanitize_primitives(self):
        assert _sanitize_response("test") == "test"
        assert _sanitize_response(123) == 123
        assert _sanitize_response(True) is True
        assert _sanitize_response(None) is None

    def test_sanitize_dict(self):
        data = {"key": "value", "number": 42}
        result = _sanitize_response(data)
        assert result == {"key": "value", "number": 42}

    def test_sanitize_removes_response_metadata(self):
        data = {"key": "value", "ResponseMetadata": {"HTTPStatusCode": 200}}
        result = _sanitize_response(data)
        assert "ResponseMetadata" not in result
        assert result == {"key": "value"}

    def test_sanitize_list_truncation(self):
        large_list = list(range(150))
        result = _sanitize_response(large_list)
        assert len(result) == 101  # 100 items + truncation message
        assert "truncated" in str(result[-1]).lower()

    def test_sanitize_bytes(self):
        data = b"binary data"
        result = _sanitize_response(data)
        assert "binary data" in result
        assert "bytes" in result

    def test_sanitize_nested_dict(self):
        data = {
            "outer": {
                "inner": {
                    "value": "test"
                }
            }
        }
        result = _sanitize_response(data)
        assert result["outer"]["inner"]["value"] == "test"


class TestExecuteAWSSDKCall:
    """Test actual AWS SDK execution (integration tests)."""

    def test_missing_service_name(self):
        result = execute_aws_sdk_call(
            service_name="",
            operation_name="list_buckets",
        )
        assert not result["success"]
        assert "required" in result["error"].lower()

    def test_missing_operation_name(self):
        result = execute_aws_sdk_call(
            service_name="s3",
            operation_name="",
        )
        assert not result["success"]
        assert "required" in result["error"].lower()

    def test_blocked_operation(self):
        result = execute_aws_sdk_call(
            service_name="s3",
            operation_name="delete_bucket",
        )
        assert not result["success"]
        assert "not allowed" in result["error"].lower()

    def test_invalid_service(self):
        result = execute_aws_sdk_call(
            service_name="invalid_service_name",
            operation_name="describe_something",
        )
        assert not result["success"]

    def test_invalid_operation(self):
        result = execute_aws_sdk_call(
            service_name="s3",
            operation_name="invalid_operation",
        )
        assert not result["success"]
        # Invalid operation gets caught by allowlist before checking if it exists
        assert "not allowed" in result["error"].lower() or "not found" in result["error"].lower()

    def test_list_s3_buckets(self):
        """Real AWS API call - list S3 buckets."""
        result = execute_aws_sdk_call(
            service_name="s3",
            operation_name="list_buckets",
        )

        # May succeed or fail depending on credentials, but should not crash
        assert "success" in result
        assert "service" in result
        assert result["service"] == "s3"
        assert result["operation"] == "list_buckets"

    def test_describe_regions(self):
        """Real AWS API call - describe EC2 regions (no credentials needed)."""
        result = execute_aws_sdk_call(
            service_name="ec2",
            operation_name="describe_regions",
        )

        # This should work even without credentials in some AWS regions
        assert "success" in result
        assert result["service"] == "ec2"

    def test_invalid_parameters(self):
        """Test with invalid parameters."""
        result = execute_aws_sdk_call(
            service_name="s3",
            operation_name="get_object",
            parameters={"InvalidParam": "value"},
        )

        # Should fail with parameter validation error
        assert not result["success"]

    def test_response_structure(self):
        """Verify response structure is consistent."""
        result = execute_aws_sdk_call(
            service_name="s3",
            operation_name="list_buckets",
        )

        # Required fields
        assert "success" in result
        assert "service" in result
        assert "operation" in result
        assert "data" in result
        assert "error" in result
        assert "metadata" in result
