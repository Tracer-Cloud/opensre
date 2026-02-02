"""Simplified ECS Fargate Prefect stack.

Creates:
- ECS Fargate service running Prefect server + worker (public subnet, no VPC creation)
- S3 buckets for landing and processed data
- Lambda function for /trigger endpoint
- API Gateway HTTP API

Simplified:
- Uses default VPC with public subnets
- No NAT gateway, no private subnets
- SQLite (ephemeral) for Prefect state
- Single ECS task runs both server and worker
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


class EcsPrefectStack(Stack):
    """Simplified ECS Fargate Prefect infrastructure stack."""

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

        # CloudWatch log group for Prefect
        log_group = logs.LogGroup(
            self,
            "PrefectLogGroup",
            log_group_name="/ecs/tracer-prefect",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ECS Cluster
        cluster = ecs.Cluster(
            self,
            "PrefectCluster",
            vpc=vpc,
            cluster_name="tracer-prefect-cluster",
            enable_fargate_capacity_providers=True,
        )

        # ECS Task Role (for S3 access)
        task_role = iam.Role(
            self,
            "PrefectTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        landing_bucket.grant_read(task_role)
        processed_bucket.grant_read_write(task_role)

        # ECS Execution Role
        execution_role = iam.Role(
            self,
            "PrefectExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )

        # Task Definition - ARM64 with sufficient resources for Prefect server + worker
        task_definition = ecs.FargateTaskDefinition(
            self,
            "PrefectTaskDef",
            cpu=512,
            memory_limit_mib=1024,
            task_role=task_role,
            execution_role=execution_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        # Container with custom pre-built image for faster startup (ARM64 platform)
        container = task_definition.add_container(
            "PrefectContainer",
            image=ecs.ContainerImage.from_asset(
                "../prefect_image",
                platform=ecr_assets.Platform.LINUX_ARM64,
            ),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="prefect",
                log_group=log_group,
            ),
            environment={
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "PROCESSED_BUCKET": processed_bucket.bucket_name,
                "PREFECT_API_URL": "http://127.0.0.1:4200/api",
                "OTEL_EXPORTER_OTLP_ENDPOINT": "127.0.0.1:4317",
                "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc",
                "OTEL_SERVICE_NAME": "prefect-etl-pipeline",
                "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_prefect,pipeline.framework=prefect,test_case=test_case_upstream_prefect_ecs_fargate",
            },
        )

        container.add_port_mappings(ecs.PortMapping(container_port=4200, protocol=ecs.Protocol.TCP))

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

        # Security group for Prefect service
        security_group = ec2.SecurityGroup(
            self,
            "PrefectSG",
            vpc=vpc,
            description="Security group for Prefect ECS service",
            allow_all_outbound=True,
        )
        security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(4200),
            "Allow Prefect API access",
        )

        # ECS Service - optimized for fast dev deployments
        ecs.FargateService(
            self,
            "PrefectService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[security_group],
            min_healthy_percent=0,
            max_healthy_percent=200,
            health_check_grace_period=Duration.seconds(0),
        )

        # Lambda for /trigger endpoint
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
                # Prefect API URL will need to be updated after deployment
                # with the ECS task public IP
                "PREFECT_API_URL": "http://localhost:4200/api",
            },
        )

        # API Gateway
        api = apigw.LambdaRestApi(
            self,
            "TriggerApi",
            handler=trigger_lambda,
            rest_api_name="tracer-prefect-trigger",
            description="API to trigger Prefect pipeline flows",
        )

        # Outputs
        CfnOutput(self, "LandingBucketName", value=landing_bucket.bucket_name)
        CfnOutput(self, "ProcessedBucketName", value=processed_bucket.bucket_name)
        CfnOutput(self, "TriggerApiUrl", value=api.url)
        CfnOutput(self, "MockApiUrl", value=mock_api.url)
        CfnOutput(self, "EcsClusterName", value=cluster.cluster_name)
        CfnOutput(self, "LogGroupName", value=log_group.log_group_name)
        CfnOutput(
            self,
            "TriggerLambdaName",
            value=trigger_lambda.function_name,
            description="Update PREFECT_API_URL env var with ECS task public IP after deployment",
        )
