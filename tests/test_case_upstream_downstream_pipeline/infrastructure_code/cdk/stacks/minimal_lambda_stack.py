"""Minimal Lambda-only upstream/downstream failure test case.

Creates:
- 3 Lambda functions (Mock API, Ingester, Mock DAG)
- 2 S3 buckets (landing, processed)
- API Gateway
- CloudWatch logs
- IAM roles

No VPC, no ECS, no Airflow. Fast deployment (~30 seconds).
"""

from aws_cdk import CfnOutput, Duration, RemovalPolicy, Stack
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_s3 as s3
from aws_cdk import aws_s3_notifications as s3n
from constructs import Construct


class MinimalLambdaTestCaseStack(Stack):
    """Minimal Lambda-only test case stack."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

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

        # Mock External API Lambda
        mock_api_lambda = lambda_.Function(
            self,
            "MockApiLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../../pipeline_code/external_vendor_api"),
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
        ingester_lambda = lambda_.Function(
            self,
            "IngesterLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../../pipeline_code/api_ingester"),
            timeout=Duration.seconds(60),
            environment={
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "EXTERNAL_API_URL": mock_api.url,
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
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../../pipeline_code/mock_dag"),
            timeout=Duration.seconds(300),
            environment={
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "PROCESSED_BUCKET": processed_bucket.bucket_name,
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
