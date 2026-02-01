"""Simplified ECS Fargate Airflow stack.

Creates:
- ECS Fargate service running Airflow 3.1.6
- S3 buckets for landing and processed data
- Lambda function for /trigger endpoint
- API Gateway HTTP API
"""

import sys
from pathlib import Path

from aws_cdk import (
    BundlingOptions,
    CfnOutput,
    Duration,
    Stack,
)
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecr_assets as ecr_assets
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from constructs import Construct

project_root = Path(__file__).resolve().parents[5]
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from tests.shared.infrastructure_code.cdk.constructs import (  # noqa: E402
    LandingProcessedBuckets,
    MockExternalApi,
    TriggerApiLambda,
    create_ecs_cluster,
    create_log_group,
)


class EcsAirflowStack(Stack):
    """Simplified ECS Fargate Airflow infrastructure stack."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        vpc = ec2.Vpc.from_lookup(self, "DefaultVpc", is_default=True)

        buckets = LandingProcessedBuckets(self, "DataBuckets")
        landing_bucket = buckets.landing_bucket
        processed_bucket = buckets.processed_bucket

        mock_api = MockExternalApi(
            self,
            "MockExternalApi",
            code_path="../../../shared/external_vendor_api",
        )

        log_group = create_log_group(
            self,
            "AirflowLogGroup",
            log_group_name="/ecs/tracer-airflow",
        )

        cluster = create_ecs_cluster(
            self,
            "AirflowCluster",
            vpc=vpc,
            cluster_name="tracer-airflow-cluster",
        )

        task_role = iam.Role(
            self,
            "AirflowTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )
        landing_bucket.grant_read(task_role)
        processed_bucket.grant_read_write(task_role)

        execution_role = iam.Role(
            self,
            "AirflowExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )

        task_definition = ecs.FargateTaskDefinition(
            self,
            "AirflowTaskDef",
            cpu=1024,
            memory_limit_mib=2048,
            task_role=task_role,
            execution_role=execution_role,
            runtime_platform=ecs.RuntimePlatform(
                cpu_architecture=ecs.CpuArchitecture.ARM64,
                operating_system_family=ecs.OperatingSystemFamily.LINUX,
            ),
        )

        container = task_definition.add_container(
            "AirflowContainer",
            image=ecs.ContainerImage.from_asset(
                "../airflow_image",
                platform=ecr_assets.Platform.LINUX_ARM64,
            ),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="airflow",
                log_group=log_group,
            ),
            environment={
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "PROCESSED_BUCKET": processed_bucket.bucket_name,
                "AIRFLOW__CORE__EXECUTOR": "LocalExecutor",
                "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": "sqlite:////opt/airflow/airflow.db",
                "AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION": "False",
                "AIRFLOW__CORE__LOAD_EXAMPLES": "False",
                "AIRFLOW__API__AUTH_BACKENDS": "airflow.api.auth.backend.basic_auth",
                "AIRFLOW__WEBSERVER__EXPOSE_CONFIG": "True",
                "AIRFLOW__CORE__FERNET_KEY": "dummy-fernet-key-for-testing-only",
                "AIRFLOW__CORE__DAGS_FOLDER": "/opt/airflow/dags",
                "AIRFLOW__CORE__SIMPLE_AUTH_MANAGER_ALL_ADMINS": "True",
                "AWS_DEFAULT_REGION": self.region,
            },
        )

        container.add_port_mappings(ecs.PortMapping(container_port=8080, protocol=ecs.Protocol.TCP))

        security_group = ec2.SecurityGroup(
            self,
            "AirflowSG",
            vpc=vpc,
            description="Security group for Airflow ECS service",
            allow_all_outbound=True,
        )
        security_group.add_ingress_rule(
            ec2.Peer.any_ipv4(),
            ec2.Port.tcp(8080),
            "Allow Airflow API access",
        )

        ecs.FargateService(
            self,
            "AirflowService",
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

        trigger_construct = TriggerApiLambda(
            self,
            "TriggerApi",
            code_path="../../pipeline_code/trigger_lambda",
            handler="handler.lambda_handler",
            runtime=lambda_.Runtime.PYTHON_3_11,
            role=trigger_lambda_role,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment={
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "PROCESSED_BUCKET": processed_bucket.bucket_name,
                "EXTERNAL_API_URL": mock_api.api.url,
                "AIRFLOW_API_URL": "http://localhost:8080/api/v2",
                "AIRFLOW_API_USERNAME": "admin",
                "AIRFLOW_API_PASSWORD": "admin",
                "AIRFLOW_DAG_ID": "upstream_downstream_pipeline_airflow",
            },
            bundling=BundlingOptions(
                image=lambda_.Runtime.PYTHON_3_11.bundling_image,
                command=[
                    "bash",
                    "-c",
                    "pip install -r requirements.txt -t /asset-output && cp -au . /asset-output",
                ],
            ),
            rest_api_name="tracer-airflow-trigger",
            description="API to trigger Airflow pipeline DAG runs",
        )
        trigger_lambda = trigger_construct.lambda_function
        api = trigger_construct.api

        CfnOutput(self, "LandingBucketName", value=landing_bucket.bucket_name)
        CfnOutput(self, "ProcessedBucketName", value=processed_bucket.bucket_name)
        CfnOutput(self, "TriggerApiUrl", value=api.url)
        CfnOutput(self, "MockApiUrl", value=mock_api.api.url)
        CfnOutput(self, "EcsClusterName", value=cluster.cluster_name)
        CfnOutput(self, "LogGroupName", value=log_group.log_group_name)
        CfnOutput(
            self,
            "TriggerLambdaName",
            value=trigger_lambda.function_name,
            description="Update AIRFLOW_API_URL env var with ECS task public IP after deployment",
        )
