"""
Tests for AWS SDK tool actions.

Integration tests - no mocks, real AWS API calls with read-only operations.
"""

from app.agent.tools.tool_actions.aws.aws_sdk_actions import execute_aws_operation


class TestExecuteAWSOperation:
    """Test the high-level AWS operation action."""

    def test_missing_service(self):
        result = execute_aws_operation(
            service="",
            operation="list_buckets",
        )
        assert not result["found"]
        assert "required" in result["error"].lower()

    def test_missing_operation(self):
        result = execute_aws_operation(
            service="s3",
            operation="",
        )
        assert not result["found"]
        assert "required" in result["error"].lower()

    def test_blocked_operation(self):
        result = execute_aws_operation(
            service="s3",
            operation="delete_bucket",
            parameters={"Bucket": "test-bucket"},
        )
        assert not result["found"]
        assert "not allowed" in result["error"].lower()

    def test_blocked_put_operation(self):
        result = execute_aws_operation(
            service="s3",
            operation="put_object",
            parameters={"Bucket": "test", "Key": "test.txt"},
        )
        assert not result["found"]
        assert "not allowed" in result["error"].lower()

    def test_list_s3_buckets(self):
        """Real AWS API call - list S3 buckets."""
        result = execute_aws_operation(
            service="s3",
            operation="list_buckets",
        )

        # Response structure validation
        assert "found" in result
        assert "service" in result
        assert result["service"] == "s3"
        assert result["operation"] == "list_buckets"

        # If successful, should have result
        if result["found"]:
            assert "result" in result
            assert "metadata" in result

    def test_describe_ec2_regions(self):
        """Real AWS API call - describe EC2 regions."""
        result = execute_aws_operation(
            service="ec2",
            operation="describe_regions",
        )

        assert "found" in result
        assert result["service"] == "ec2"
        assert result["operation"] == "describe_regions"

    def test_get_caller_identity(self):
        """Real AWS API call - STS get caller identity."""
        result = execute_aws_operation(
            service="sts",
            operation="get_caller_identity",
        )

        assert "found" in result
        assert result["service"] == "sts"

        # If credentials are configured, this should succeed
        if result["found"]:
            assert "result" in result
            # STS returns Account, UserId, Arn
            assert isinstance(result["result"], dict)

    def test_list_lambda_functions_with_parameters(self):
        """Test with parameters - list Lambda functions."""
        result = execute_aws_operation(
            service="lambda",
            operation="list_functions",
            parameters={"MaxItems": 10},
        )

        assert "found" in result
        assert result["service"] == "lambda"
        assert result["operation"] == "list_functions"

    def test_invalid_service(self):
        """Test with invalid service name."""
        result = execute_aws_operation(
            service="invalid_service",
            operation="describe_something",
        )

        assert not result["found"]
        assert "error" in result

    def test_invalid_operation(self):
        """Test with invalid operation name."""
        result = execute_aws_operation(
            service="s3",
            operation="invalid_operation_xyz",
        )

        assert not result["found"]
        assert "not found" in result["error"].lower() or "not allowed" in result["error"].lower()

    def test_response_structure_on_error(self):
        """Verify error response structure."""
        result = execute_aws_operation(
            service="s3",
            operation="delete_bucket",
        )

        # Error response structure
        assert "found" in result
        assert result["found"] is False
        assert "service" in result
        assert "operation" in result
        assert "error" in result

    def test_response_structure_on_success(self):
        """Verify success response structure."""
        result = execute_aws_operation(
            service="sts",
            operation="get_caller_identity",
        )

        # Success response structure (if credentials available)
        assert "found" in result
        assert "service" in result
        assert "operation" in result

        if result["found"]:
            assert "result" in result
            assert "metadata" in result
