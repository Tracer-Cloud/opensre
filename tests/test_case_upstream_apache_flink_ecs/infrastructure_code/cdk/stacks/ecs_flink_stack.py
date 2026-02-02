"""ECS Fargate Flink batch processing stack.

Creates:
- ECS cluster for running Flink batch tasks
- ECS task definition for PyFlink container
- S3 buckets for landing and processed data
- Lambda function for /trigger endpoint (ingestion + ECS task launcher)
- API Gateway HTTP API
- Mock External Vendor API Lambda (shared)

Key difference from Prefect stack:
- No long-running service (tasks run on-demand and exit)
- Trigger Lambda starts ECS tasks via RunTask API
"""

import sys
from pathlib import Path

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct

project_root = Path(__file__).resolve().parents[5]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.shared.infrastructure_code.cdk.constructs import (  # noqa: E402
    AlloySidecar,
    GrafanaCloudSecrets,
)


class EcsFlinkStack(Stack):
    """ECS Fargate Flink batch processing infrastructure stack."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)
        secret_name = self.node.try_get_context("grafana_secret_name") or "tracer/grafana-cloud"
        grafana_secrets = GrafanaCloudSecrets(
            self, "GrafanaSecrets", secret_name=secret_name
        )

        # Use default VPC (no new VPC creation)
        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        # S3 buckets
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

        # CloudWatch log group for Flink tasks
        log_group = logs.LogGroup(
            self,
            "FlinkLogGroup",
            log_group_name="/ecs/tracer-flink",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ECS Cluster
        cluster = ecs.Cluster(
            self,
            "FlinkCluster",
            vpc=vpc,
            cluster_name="tracer-flink-cluster",
            enable_fargate_capacity_providers=True,
        )

        # ECS Task Role (for S3 access)
        task_role = iam.Role(
            self,
            "FlinkTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        landing_bucket.grant_read(task_role)
        processed_bucket.grant_read_write(task_role)

        # ECS Execution Role
        execution_role = iam.Role(
            self,
            "FlinkExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )

        # Task Definition - ARM64 for cost efficiency
        task_definition = ecs.FargateTaskDefinition(
            self,
            "FlinkTaskDef",
            cpu=512,
            memory_limit_mib=1024,
            task_role=task_role,
            execution_role=execution_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        # Container with PyFlink image (ARM64 platform)
        container = task_definition.add_container(
            "FlinkContainer",
            image=ecs.ContainerImage.from_asset(
                "../flink_image",
                platform=ecr_assets.Platform.LINUX_ARM64,
            ),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="flink",
                log_group=log_group,
            ),
            environment={
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "PROCESSED_BUCKET": processed_bucket.bucket_name,
                "OTEL_EXPORTER_OTLP_ENDPOINT": "127.0.0.1:4317",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
                "OTEL_SERVICE_NAME": "flink-etl-pipeline",
                "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_flink,pipeline.framework=flink,test_case=test_case_upstream_apache_flink_ecs",
            },
        )

        alloy_sidecar = AlloySidecar(
            self,
            "AlloySidecar",
            task_definition=task_definition,
            log_group=log_group,
            grafana_secrets=grafana_secrets,
        )
        container.add_container_dependencies(
            ecs.ContainerDependency(
                container=alloy_sidecar.container,
                condition=ecs.ContainerDependencyCondition.START,
            )
        )

        # Security group for Flink tasks (outbound only)
        security_group = ec2.SecurityGroup(
            self,
            "FlinkSG",
            vpc=vpc,
            description="Security group for Flink ECS tasks",
            allow_all_outbound=True,
        )

        # Get subnet IDs for Fargate tasks
        public_subnets = vpc.select_subnets(subnet_type=ec2.SubnetType.PUBLIC)

        # Lambda Role with ECS RunTask permissions
        trigger_lambda_role = iam.Role(
            self,
            "TriggerLambdaRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )
        landing_bucket.grant_write(trigger_lambda_role)

        # Grant ECS RunTask permissions
        trigger_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["ecs:RunTask"],
                resources=[task_definition.task_definition_arn],
            )
        )
        trigger_lambda_role.add_to_policy(
            iam.PolicyStatement(
                actions=["iam:PassRole"],
                resources=[task_role.role_arn, execution_role.role_arn],
            )
        )

        # Trigger Lambda with bundled dependencies
        trigger_lambda = lambda_.Function(
            self,
            "TriggerLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                "../../pipeline_code/trigger_lambda",
                bundling=BundlingOptions(
                    image=lambda_.Runtime.PYTHON_3_11.bundling_image,
                    command=[
                        "bash",
                        "-c",
                        "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output",
                    ],
                ),
            ),
            role=trigger_lambda_role,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment={
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "PROCESSED_BUCKET": processed_bucket.bucket_name,
                "EXTERNAL_API_URL": mock_api.url,
                "ECS_CLUSTER": cluster.cluster_arn,
                "TASK_DEFINITION": task_definition.task_definition_arn,
                "SUBNET_IDS": ",".join(public_subnets.subnet_ids),
                "SECURITY_GROUP_ID": security_group.security_group_id,
            },
        )

        # API Gateway
        api = apigw.LambdaRestApi(
            self,
            "TriggerApi",
            handler=trigger_lambda,
            rest_api_name="tracer-flink-trigger",
            description="API to trigger Flink batch processing jobs",
        )

        # Outputs
        CfnOutput(self, "LandingBucketName", value=landing_bucket.bucket_name)
        CfnOutput(self, "ProcessedBucketName", value=processed_bucket.bucket_name)
        CfnOutput(self, "TriggerApiUrl", value=api.url)
        CfnOutput(self, "MockApiUrl", value=mock_api.url)
        CfnOutput(self, "EcsClusterName", value=cluster.cluster_name)
        CfnOutput(self, "EcsClusterArn", value=cluster.cluster_arn)
        CfnOutput(self, "TaskDefinitionArn", value=task_definition.task_definition_arn)
        CfnOutput(self, "LogGroupName", value=log_group.log_group_name)
        CfnOutput(self, "SecurityGroupId", value=security_group.security_group_id)
        CfnOutput(self, "SubnetIds", value=",".join(public_subnets.subnet_ids))
