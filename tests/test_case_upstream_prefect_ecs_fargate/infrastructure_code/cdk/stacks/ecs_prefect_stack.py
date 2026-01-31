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

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import aws_apigateway as apigw
from aws_cdk import aws_ec2 as ec2
from aws_cdk import aws_ecs as ecs
from aws_cdk import aws_iam as iam
from aws_cdk import aws_lambda as lambda_
from aws_cdk import aws_logs as logs
from aws_cdk import aws_s3 as s3
from constructs import Construct


class EcsPrefectStack(Stack):
    """Simplified ECS Fargate Prefect infrastructure stack."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

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

        # Mock External API Lambda (reuse from upstream/downstream test case)
        mock_api_lambda = lambda_.Function(
            self,
            "MockApiLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset(
                "../../../test_case_upstream_downstream_pipeline/pipeline_code/external_vendor_api"
            ),
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

        # Task Definition - runs Prefect server + worker in single container
        task_definition = ecs.FargateTaskDefinition(
            self,
            "PrefectTaskDef",
            cpu=512,
            memory_limit_mib=1024,
            task_role=task_role,
            execution_role=execution_role,
        )

        # Container running Prefect server and worker
        container = task_definition.add_container(
            "PrefectContainer",
            image=ecs.ContainerImage.from_registry("prefecthq/prefect:3-python3.11"),
            logging=ecs.LogDrivers.aws_logs(
                stream_prefix="prefect",
                log_group=log_group,
            ),
            environment={
                "PREFECT_SERVER_API_HOST": "0.0.0.0",
                "PREFECT_SERVER_API_PORT": "4200",
                "PREFECT_API_URL": "http://localhost:4200/api",
                "LANDING_BUCKET": landing_bucket.bucket_name,
                "PROCESSED_BUCKET": processed_bucket.bucket_name,
            },
            # Start server, wait, then start worker
            command=[
                "bash",
                "-c",
                "prefect server start --host 0.0.0.0 & "
                "sleep 10 && "
                "prefect work-pool create default-pool --type process 2>/dev/null || true && "
                "prefect worker start --pool default-pool",
            ],
        )

        container.add_port_mappings(ecs.PortMapping(container_port=4200, protocol=ecs.Protocol.TCP))

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

        # ECS Service - runs in public subnet with public IP
        ecs.FargateService(
            self,
            "PrefectService",
            cluster=cluster,
            task_definition=task_definition,
            desired_count=1,
            assign_public_ip=True,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PUBLIC),
            security_groups=[security_group],
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

        # Trigger Lambda
        trigger_lambda = lambda_.Function(
            self,
            "TriggerLambda",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../../pipeline_code/trigger_lambda"),
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
