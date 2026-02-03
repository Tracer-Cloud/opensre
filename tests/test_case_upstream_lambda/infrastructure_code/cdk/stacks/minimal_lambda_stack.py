"""Minimal Lambda-only upstream/downstream failure test case.

Creates:
- 3 Lambda functions (Mock API, Ingester, Mock DAG)
- 2 S3 buckets (landing, processed)
- API Gateway
- CloudWatch logs
- IAM roles

No VPC, no ECS, no Airflow. Fast deployment (~30 seconds).
"""

import sys
from pathlib import Path

from aws_cdk import BundlingOptions, CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from constructs import Construct

project_root = Path(__file__).resolve().parents[5]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.shared.infrastructure_code.cdk.constructs import (  # noqa: E402
    GrafanaCloudSecrets,
)


class MinimalLambdaTestCaseStack(Stack):
    """Minimal Lambda-only test case stack."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        secret_name = self.node.try_get_context("grafana_secret_name") or "tracer/grafana-cloud"
        grafana_secrets = GrafanaCloudSecrets(
            self, "GrafanaSecrets", secret_name=secret_name
        )

        # S3 buckets (CloudFormation generates unique names)
        landing_bucket = s3.Bucket(
            self,
            "LandingBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        processed_bucket = s3.Bucket(
            self,
            "ProcessedBucket",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
        )

        # Mock External API Lambda (shared across test cases)
        mock_api_lambda = lambda_.Function(
            self,
            "MockApiLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../../../shared/external_vendor_api"),
            timeout=Duration.seconds(30),
            memory_size=128,
        )

        # API Gateway for Mock API
        mock_api = apigw.LambdaRestApi(
            self,
            "MockExternalApi",
            handler=mock_api_lambda,
        )

        # Ingester Lambda
        bundling = BundlingOptions(
            image=lambda_.Runtime.PYTHON_3_11.bundling_image,
            command=[
                "bash",
                "-c",
                "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output",
            ],
        )
        ingester_lambda = lambda_.Function(
            self,
            "IngesterLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="api_ingester.handler.lambda_handler",
            code=lambda_.Code.from_asset("../../pipeline_code", bundling=bundling),
            timeout=Duration.seconds(60),
            environment={
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "EXTERNAL_API_URL": mock_api.url,
                "OTEL_EXPORTER_OTLP_ENDPOINT": grafana_secrets.secret.secret_value_from_json(
                    "GCLOUD_OTLP_ENDPOINT"
                ).unsafe_unwrap(),
                "GCLOUD_OTLP_AUTH_HEADER": grafana_secrets.secret.secret_value_from_json(
                    "GCLOUD_OTLP_AUTH_HEADER"
                ).unsafe_unwrap(),
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "OTEL_SERVICE_NAME": "lambda-api-ingester",
                "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_lambda_ingester,pipeline.framework=lambda,test_case=test_case_upstream_lambda",
            },
        )
        landing_bucket.grant_write(ingester_lambda)

        # API Gateway for Ingester (HTTP trigger)
        ingester_api = apigw.LambdaRestApi(
            self,
            "IngesterApi",
            handler=ingester_lambda,
            description="HTTP endpoint to trigger data ingestion pipeline",
        )

        # Mock DAG Lambda (orchestration placeholder)
        mock_dag_lambda = lambda_.Function(
            self,
            "MockDagLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="mock_dag.handler.lambda_handler",
            code=lambda_.Code.from_asset("../../pipeline_code", bundling=bundling),
            timeout=Duration.seconds(300),
            environment={
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "PROCESSED_BUCKET": processed_bucket.bucket_name,
                "OTEL_EXPORTER_OTLP_ENDPOINT": grafana_secrets.secret.secret_value_from_json(
                    "GCLOUD_OTLP_ENDPOINT"
                ).unsafe_unwrap(),
                "GCLOUD_OTLP_AUTH_HEADER": grafana_secrets.secret.secret_value_from_json(
                    "GCLOUD_OTLP_AUTH_HEADER"
                ).unsafe_unwrap(),
                "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
                "OTEL_SERVICE_NAME": "lambda-mock-dag",
                "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_lambda,pipeline.framework=lambda,test_case=test_case_upstream_lambda",
            },
        )
        landing_bucket.grant_read(mock_dag_lambda)
        processed_bucket.grant_write(mock_dag_lambda)

        # Trigger Mock DAG on S3 upload
        landing_bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(mock_dag_lambda),
        )

        # Outputs
        CfnOutput(self, "MockApiUrl", value=mock_api.url)
        CfnOutput(
            self,
            "IngesterApiUrl",
            value=ingester_api.url,
            description="HTTP endpoint to trigger pipeline",
        )
        CfnOutput(self, "IngesterFunctionName", value=ingester_lambda.function_name)
        CfnOutput(self, "MockDagFunctionName", value=mock_dag_lambda.function_name)
        CfnOutput(self, "LandingBucketName", value=landing_bucket.bucket_name)
        CfnOutput(self, "ProcessedBucketName", value=processed_bucket.bucket_name)
