"""Direct unit tests for integrations/aws/aws_sdk_client.py."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import MagicMock, patch

import pytest
from botocore.exceptions import ClientError, NoCredentialsError, ParamValidationError

from integrations.aws.aws_sdk_client import (
    MAX_LIST_ITEMS,
    _is_operation_allowed,
    _sanitize_response,
    execute_aws_sdk_call,
)


class TestIsOperationAllowedAllowlist:
    @pytest.mark.parametrize(
        "op",
        [
            "describe_instances",
            "describe_db_instances",
            "get_role",
            "get_function_configuration",
            "list_functions",
            "list_clusters",
            "head_object",
            "head_bucket",
            "query",
            "scan",
            "select_object_content",
            "batch_get_item",
            "lookup_events",
        ],
    )
    def test_allowed_operations(self, op: str) -> None:
        allowed, reason = _is_operation_allowed(op)
        assert allowed is True, f"{op} should be allowed: {reason}"
        assert reason == "Operation allowed"


class TestIsOperationAllowedBlocklist:
    @pytest.mark.parametrize(
        "op",
        [
            "delete_bucket",
            "remove_tags",
            "update_function_configuration",
            "put_object",
            "create_stack",
            "modify_db_instance",
            "terminate_instances",
            "stop_instances",
            "start_instances",
            "reboot_instances",
            "attach_volume",
            "detach_volume",
            "associate_route_table",
            "disassociate_route_table",
        ],
    )
    def test_blocked_operations(self, op: str) -> None:
        allowed, reason = _is_operation_allowed(op)
        assert allowed is False, f"{op} should be blocked"
        assert "blocked pattern" in reason

    def test_blocklist_takes_precedence_over_allowlist(self) -> None:
        allowed, _ = _is_operation_allowed("get_and_delete_item")
        assert allowed is False

    def test_unknown_operation_rejected(self) -> None:
        allowed, reason = _is_operation_allowed("invoke_function")
        assert allowed is False
        assert "does not match any allowed patterns" in reason

    def test_case_insensitivity(self) -> None:
        allowed, _ = _is_operation_allowed("Describe_Instances")
        assert allowed is True

        allowed, _ = _is_operation_allowed("Delete_Bucket")
        assert allowed is False


class TestSanitizeResponseDatetime:
    def test_datetime_converted_to_iso(self) -> None:
        dt = datetime(2024, 6, 15, 12, 30, 0, tzinfo=UTC)
        assert _sanitize_response(dt) == "2024-06-15T12:30:00+00:00"

    def test_datetime_nested_in_dict(self) -> None:
        dt = datetime(2024, 1, 1, tzinfo=UTC)
        result = _sanitize_response({"LaunchTime": dt})
        assert result["LaunchTime"] == "2024-01-01T00:00:00+00:00"


class TestSanitizeResponseBytes:
    def test_bytes_replaced_with_placeholder(self) -> None:
        result = _sanitize_response(b"\x00\x01\x02")
        assert result == "<binary data: 3 bytes>"

    def test_bytes_nested_in_dict(self) -> None:
        result = _sanitize_response({"Body": b"hello"})
        assert result["Body"] == "<binary data: 5 bytes>"


class TestSanitizeResponseDeepNesting:
    def test_max_depth_reached(self) -> None:
        default_max_depth = 10
        data: dict = {"leaf": "value"}
        for _ in range(default_max_depth + 2):
            data = {"nested": data}
        result = _sanitize_response(data)

        current = result
        for _ in range(default_max_depth + 1):
            current = current["nested"]
        assert current == "... (max depth reached)"

    def test_custom_max_depth(self) -> None:
        data: dict = {"leaf": "value"}
        for _ in range(5):
            data = {"nested": data}
        result = _sanitize_response(data, max_depth=3)

        current = result
        for _ in range(4):
            current = current["nested"]
        assert current == "... (max depth reached)"


class TestSanitizeResponseOversizedLists:
    def test_list_over_max_is_truncated(self) -> None:
        big_list = list(range(MAX_LIST_ITEMS + 50))
        result = _sanitize_response(big_list)
        assert len(result) == MAX_LIST_ITEMS + 1
        assert "50 more items truncated" in result[-1]

    def test_list_exactly_at_max_not_truncated(self) -> None:
        exact_list = list(range(MAX_LIST_ITEMS))
        result = _sanitize_response(exact_list)
        assert len(result) == MAX_LIST_ITEMS

    def test_list_under_max_not_truncated(self) -> None:
        small_list = [1, 2, 3]
        result = _sanitize_response(small_list)
        assert result == [1, 2, 3]


class TestSanitizeResponseMisc:
    def test_none_returns_none(self) -> None:
        assert _sanitize_response(None) is None

    def test_primitive_passthrough(self) -> None:
        assert _sanitize_response(42) == 42
        assert _sanitize_response("hello") == "hello"
        assert _sanitize_response(3.14) == 3.14

    def test_response_metadata_stripped(self) -> None:
        data = {
            "Instances": [{"InstanceId": "i-1"}],
            "ResponseMetadata": {"RequestId": "abc", "HTTPStatusCode": 200},
        }
        result = _sanitize_response(data)
        assert "ResponseMetadata" not in result
        assert result["Instances"] == [{"InstanceId": "i-1"}]

    def test_tuple_handled(self) -> None:
        result = _sanitize_response((1, 2, 3))
        assert result == [1, 2, 3]

    def test_complex_nested_structure(self) -> None:
        dt = datetime(2024, 6, 1, tzinfo=UTC)
        data = {
            "Reservations": [
                {
                    "Instances": [
                        {
                            "InstanceId": "i-abc",
                            "LaunchTime": dt,
                            "Tags": [{"Key": "Name", "Value": "web"}],
                        }
                    ]
                }
            ],
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }
        result = _sanitize_response(data)
        assert "ResponseMetadata" not in result
        inst = result["Reservations"][0]["Instances"][0]
        assert inst["InstanceId"] == "i-abc"
        assert inst["LaunchTime"] == "2024-06-01T00:00:00+00:00"


@pytest.fixture()
def mock_boto3_client():
    fake_client = MagicMock()
    fake_client.meta.region_name = "us-east-1"
    with patch("integrations.aws.aws_sdk_client.boto3.client", return_value=fake_client):
        yield fake_client


class TestExecuteAwsSdkCallHappyPath:
    def test_success_with_no_params(self, mock_boto3_client) -> None:
        mock_boto3_client.describe_instances.return_value = {
            "Reservations": [],
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }
        result = execute_aws_sdk_call("ec2", "describe_instances")
        assert result["success"] is True
        assert result["service"] == "ec2"
        assert result["operation"] == "describe_instances"
        assert "ResponseMetadata" not in result["data"]
        assert result["metadata"]["region"] == "us-east-1"

    def test_success_with_params(self, mock_boto3_client) -> None:
        mock_boto3_client.describe_instances.return_value = {
            "Reservations": [{"Instances": [{"InstanceId": "i-1234"}]}],
            "ResponseMetadata": {"HTTPStatusCode": 200},
        }
        params = {"Filters": [{"Name": "instance-state-name", "Values": ["running"]}]}
        result = execute_aws_sdk_call("ec2", "describe_instances", parameters=params)
        assert result["success"] is True
        mock_boto3_client.describe_instances.assert_called_once_with(**params)

    def test_region_override(self, mock_boto3_client) -> None:
        mock_boto3_client.list_clusters.return_value = {"ClusterArns": []}
        with patch(
            "integrations.aws.aws_sdk_client.boto3.client", return_value=mock_boto3_client
        ) as m:
            execute_aws_sdk_call("ecs", "list_clusters", region="eu-west-1")
            m.assert_called_once_with("ecs", region_name="eu-west-1")


class TestExecuteAwsSdkCallValidation:
    def test_empty_service_name(self) -> None:
        result = execute_aws_sdk_call("", "describe_instances")
        assert result["success"] is False
        assert "required" in result["error"]

    def test_empty_operation_name(self) -> None:
        result = execute_aws_sdk_call("ec2", "")
        assert result["success"] is False
        assert "required" in result["error"]

    def test_blocked_operation(self) -> None:
        result = execute_aws_sdk_call("ec2", "terminate_instances")
        assert result["success"] is False
        assert "not allowed" in result["error"].lower()
        assert result["metadata"]["validation_failed"] is True

    def test_unknown_operation_pattern(self) -> None:
        result = execute_aws_sdk_call("ec2", "invoke_magic")
        assert result["success"] is False
        assert "not allowed" in result["error"].lower()

    def test_rejected_operation_does_not_create_client(self) -> None:
        with patch("integrations.aws.aws_sdk_client.boto3.client") as client:
            result = execute_aws_sdk_call("ec2", "terminate_instances")

        assert result["success"] is False
        client.assert_not_called()


class TestExecuteAwsSdkCallMissingOperation:
    def test_operation_not_found_on_service(self, mock_boto3_client) -> None:
        # MagicMock creates arbitrary attributes unless they are explicitly deleted.
        del mock_boto3_client.describe_nonexistent
        result = execute_aws_sdk_call("ec2", "describe_nonexistent")
        assert result["success"] is False
        assert "not found" in result["error"].lower()


class TestExecuteAwsSdkCallCredentialErrors:
    def test_no_credentials_error_when_creating_client(self) -> None:
        with patch(
            "integrations.aws.aws_sdk_client.boto3.client",
            side_effect=NoCredentialsError(),
        ):
            result = execute_aws_sdk_call("ec2", "describe_instances")

        assert result["success"] is False
        assert "credentials" in result["error"].lower()
        assert result["metadata"]["error_type"] == "credentials"

    def test_no_credentials_error(self, mock_boto3_client) -> None:
        mock_boto3_client.describe_instances.side_effect = NoCredentialsError()
        result = execute_aws_sdk_call("ec2", "describe_instances")
        assert result["success"] is False
        assert "credentials" in result["error"].lower()
        assert result["metadata"]["error_type"] == "credentials"


class TestExecuteAwsSdkCallParamValidation:
    def test_param_validation_error(self, mock_boto3_client) -> None:
        mock_boto3_client.describe_instances.side_effect = ParamValidationError(
            report="Missing required param: InstanceIds"
        )
        result = execute_aws_sdk_call("ec2", "describe_instances", parameters={"bad": "param"})
        assert result["success"] is False
        assert "invalid parameters" in result["error"].lower()
        assert result["metadata"]["error_type"] == "validation"


class TestExecuteAwsSdkCallClientError:
    def test_client_error(self, mock_boto3_client) -> None:
        mock_boto3_client.describe_instances.side_effect = ClientError(
            {
                "Error": {"Code": "UnauthorizedAccess", "Message": "Not authorized"},
                "ResponseMetadata": {"HTTPStatusCode": 403},
            },
            "DescribeInstances",
        )
        result = execute_aws_sdk_call("ec2", "describe_instances")
        assert result["success"] is False
        assert "UnauthorizedAccess" in result["error"]
        assert result["metadata"]["error_type"] == "client_error"
        assert result["metadata"]["error_code"] == "UnauthorizedAccess"
        assert result["metadata"]["status_code"] == 403


class TestExecuteAwsSdkCallUnexpectedError:
    def test_unexpected_runtime_error(self, mock_boto3_client) -> None:
        mock_boto3_client.describe_instances.side_effect = RuntimeError("boom")
        result = execute_aws_sdk_call("ec2", "describe_instances")
        assert result["success"] is False
        assert "unexpected error" in result["error"].lower()
        assert result["metadata"]["error_type"] == "unexpected"


class TestSecretsNeverReachTheModel:
    @pytest.mark.parametrize(
        ("service", "operation", "parameters"),
        [
            ("secretsmanager", "get_secret_value", {"SecretId": "prod/db"}),
            ("secretsmanager", "batch_get_secret_value", {"SecretIdList": ["a"]}),
            ("sts", "get_session_token", None),
            ("sts", "get_federation_token", {"Name": "x"}),
            ("ecr", "get_authorization_token", None),
            ("redshift", "get_cluster_credentials", {"DbUser": "u", "ClusterIdentifier": "c"}),
            ("ec2", "get_password_data", {"InstanceId": "i-1"}),
            ("ssm", "get_parameter", {"Name": "/prod/db", "WithDecryption": True}),
            ("ssm", "get_parameters_by_path", {"Path": "/prod", "WithDecryption": True}),
            ("apigateway", "get_api_key", {"apiKey": "k1", "includeValue": True}),
            ("apigateway", "get_api_keys", {"includeValues": True}),
            ("appsync", "list_api_keys", {"apiId": "a1"}),
            ("apigateway", "get_usage_plan_key", {"usagePlanId": "p", "keyId": "k"}),
            ("apigateway", "get_usage_plan_keys", {"usagePlanId": "p"}),
            ("quicksight", "get_dashboard_embed_url", {"AwsAccountId": "1", "DashboardId": "d"}),
            ("quicksight", "get_session_embed_url", {"AwsAccountId": "1"}),
            ("amplify", "get_artifact_url", {"artifactId": "a"}),
            ("kinesis-video-archived-media", "get_hls_streaming_session_url", {}),
            ("ecr", "get_download_url_for_layer", {"repositoryName": "r", "layerDigest": "d"}),
        ],
    )
    def test_secret_returning_calls_are_refused_before_any_client(
        self, service: str, operation: str, parameters: dict[str, object] | None
    ) -> None:
        with patch("integrations.aws.aws_sdk_client.boto3.client") as client:
            result = execute_aws_sdk_call(service, operation, parameters=parameters)

        assert result["success"] is False
        assert result["metadata"]["validation_failed"] is True
        client.assert_not_called()

    def test_ordinary_reads_stay_allowed(self) -> None:
        for operation in (
            "get_role",
            "get_parameter",
            "describe_secret",
            "list_secrets",
            "get_queue_url",
        ):
            assert _is_operation_allowed(operation)[0], operation

    def test_secret_fields_in_a_response_are_redacted(self, mock_boto3_client) -> None:
        mock_boto3_client.describe_instances.return_value = {
            "Reservations": [
                {
                    "Instances": [{"InstanceId": "i-1"}],
                    "Credentials": {"SecretAccessKey": "abc", "SessionToken": "tok"},
                }
            ],
            "Items": [{"SecretString": "hunter2", "authorizationToken": "Zm9v", "Name": "n"}],
        }

        result = execute_aws_sdk_call("ec2", "describe_instances")

        text = str(result["data"])
        assert result["success"] is True
        assert "abc" not in text and "tok" not in text
        assert "hunter2" not in text and "Zm9v" not in text
        assert "i-1" in text and "'Name': 'n'" in text

    def test_lightsail_camel_case_secrets_are_redacted(self, mock_boto3_client) -> None:
        # Shape returned by lightsail.get_instance_access_details.
        mock_boto3_client.get_instance_access_details.return_value = {
            "accessDetails": {
                "username": "admin",
                "privateKey": "-----BEGIN RSA PRIVATE KEY-----abc",
                "password": "Lightsail#1",
                "ipAddress": "203.0.113.5",
            }
        }

        result = execute_aws_sdk_call("lightsail", "get_instance_access_details")

        details = result["data"]["accessDetails"]
        assert result["success"] is True
        assert details["privateKey"] == details["password"] == "<redacted>"
        assert details["username"] == "admin" and details["ipAddress"] == "203.0.113.5"

    def test_secret_fields_are_found_by_name_part(self, mock_boto3_client) -> None:
        # Amplify get_app nests base64 user:password credentials under several names.
        mock_boto3_client.get_app.return_value = {
            "app": {
                "appId": "d1",
                "basicAuthCredentials": "dXNlcjpwYXNz",
                "autoBranchCreationConfig": {"basicAuthCredentials": "dXNlcjpwYXNz"},
                "MasterUserPassword": "pw",
            },
            "SecretArn": "arn:aws:secretsmanager:us-east-1:1:secret:x",
            "NextToken": "page-2",
        }

        result = execute_aws_sdk_call("amplify", "get_app", parameters={"appId": "d1"})

        text = str(result["data"])
        assert "dXNlcjpwYXNz" not in text and "'pw'" not in text
        assert result["data"]["SecretArn"].startswith("arn:")
        assert result["data"]["NextToken"] == "page-2"
        assert result["data"]["app"]["appId"] == "d1"

    def test_lambda_package_download_urls_are_redacted(self, mock_boto3_client) -> None:
        url = "https://awslambda-us-east-1-tasks.s3.amazonaws.com/x?X-Amz-Signature=abc"
        mock_boto3_client.get_function.return_value = {
            "Configuration": {"FunctionName": "prod", "Runtime": "python3.13"},
            "Code": {"RepositoryType": "S3", "Location": url},
        }

        result = execute_aws_sdk_call("lambda", "get_function", parameters={"FunctionName": "prod"})

        assert result["data"]["Code"]["Location"] == "<redacted>"
        assert result["data"]["Code"]["RepositoryType"] == "S3"
        assert result["data"]["Configuration"]["Runtime"] == "python3.13"

    def test_presigned_urls_are_redacted_wherever_they_appear(self, mock_boto3_client) -> None:
        # Translate get_terminology embeds one under a field not named like a URL.
        url = (
            "https://s3.amazonaws.com/b/terms.csv?X-Amz-Algorithm=AWS4-HMAC-SHA256"
            "&X-Amz-Credential=AKIA%2F20261122&X-Amz-Signature=abc"
        )
        mock_boto3_client.get_terminology.return_value = {
            "TerminologyProperties": {"Name": "glossary"},
            "TerminologyDataLocation": {"RepositoryType": "S3", "Location": url},
            "Docs": "https://docs.aws.amazon.com/translate/",
        }

        result = execute_aws_sdk_call("translate", "get_terminology", parameters={"Name": "g"})

        data = result["data"]
        assert data["TerminologyDataLocation"]["Location"] == "<redacted>"
        assert data["TerminologyProperties"]["Name"] == "glossary"
        assert data["Docs"] == "https://docs.aws.amazon.com/translate/"

    def test_ivs_stream_keys_are_redacted(self, mock_boto3_client) -> None:
        mock_boto3_client.get_stream_key.return_value = {
            "streamKey": {"arn": "arn:aws:ivs:k", "value": "sk_us-west-2_secret"}
        }

        result = execute_aws_sdk_call("ivs", "get_stream_key", parameters={"arn": "arn:aws:ivs:k"})

        assert "sk_us-west-2_secret" not in str(result["data"])
