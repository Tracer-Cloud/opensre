"""ECS Fargate Airflow test case CDK stack.

Creates:
- VPC with public/private subnets
- S3 bucket for DAGs
- S3 bucket for test data
- ECS Fargate cluster with Airflow (scheduler, webserver, worker)
- Lambda function for API ingestion
- Lambda function for mock external API (Phase 1)
- API Gateway for stable endpoint
- IAM roles with least privilege
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
)
from aws_cdk import (
    aws_apigateway as apigw,
)
from aws_cdk import (
    aws_ec2 as ec2,
)
from aws_cdk import (
    aws_ecs as ecs,
)
from aws_cdk import (
    aws_ecs_patterns as ecs_patterns,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_lambda as lambda_,
)
from aws_cdk import (
    aws_logs as logs,
)
from aws_cdk import (
    aws_s3 as s3,
)
from aws_cdk import (
    aws_servicediscovery as servicediscovery,
)
from constructs import Construct


class EcsAirflowTestCaseStack(Stack):
    """ECS Fargate Airflow test case infrastructure stack."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Environment name prefix for all resources
        env_name = "tracer-test"

        # VPC for ECS Fargate
        vpc = ec2.Vpc(
            self,
            "AirflowVpc",
            max_azs=2,
            nat_gateways=1,
            subnet_configuration=[
                ec2.SubnetConfiguration(
                    name="Public",
                    subnet_type=ec2.SubnetType.PUBLIC,
                    cidr_mask=24,
                ),
                ec2.SubnetConfiguration(
                    name="Private",
                    subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS,
                    cidr_mask=24,
                ),
            ],
        )

        # Security group for Airflow services
        airflow_sg = ec2.SecurityGroup(
            self,
            "AirflowSG",
            vpc=vpc,
            description="Security group for Airflow services",
            allow_all_outbound=True,
        )
        airflow_sg.add_ingress_rule(
            airflow_sg,
            ec2.Port.all_traffic(),
            "Allow self-referencing traffic",
        )
        airflow_sg.add_ingress_rule(
            ec2.Peer.ipv4(vpc.vpc_cidr_block),
            ec2.Port.tcp(8080),
            "Allow Airflow webserver access from VPC",
        )

        # S3 bucket for DAGs (CloudFormation generates unique name)
        dags_bucket = s3.Bucket(
            self,
            "DagsBucket",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # S3 bucket for test data (CloudFormation generates unique name)
        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # CloudWatch log groups for Airflow
        airflow_log_group = logs.LogGroup(
            self,
            "AirflowLogGroup",
            log_group_name=f"/ecs/{env_name}-airflow",
            retention=logs.RetentionDays.ONE_WEEK,
            removal_policy=RemovalPolicy.DESTROY,
        )

        # ECS Cluster
        cluster = ecs.Cluster(
            self,
            "AirflowCluster",
            vpc=vpc,
            cluster_name=f"{env_name}-airflow-cluster",
            enable_fargate_capacity_providers=True,
        )

        # Service discovery namespace (created but not currently used)
        servicediscovery.PrivateDnsNamespace(
            self,
            "AirflowNamespace",
            vpc=vpc,
            name=f"{env_name}-airflow.local",
        )

        # IAM role for Airflow tasks
        airflow_task_role = iam.Role(
            self,
            "AirflowTaskRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
        )

        # Airflow task role policies
        dags_bucket.grant_read(airflow_task_role)
        data_bucket.grant_read_write(airflow_task_role)

        airflow_task_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[airflow_log_group.log_group_arn],
            )
        )

        # IAM role for Airflow execution
        airflow_execution_role = iam.Role(
            self,
            "AirflowExecutionRole",
            assumed_by=iam.ServicePrincipal("ecs-tasks.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AmazonECSTaskExecutionRolePolicy"
                )
            ],
        )

        airflow_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogGroup",
                    "logs:CreateLogStream",
                    "logs:PutLogEvents",
                ],
                resources=[airflow_log_group.log_group_arn],
            )
        )

        # Airflow environment variables (using LocalExecutor for simplicity)
        airflow_env = {
            "AIRFLOW__CORE__EXECUTOR": "LocalExecutor",
            "AIRFLOW__DATABASE__SQL_ALCHEMY_CONN": "sqlite:////tmp/airflow.db",
            "AIRFLOW__CORE__DAGS_ARE_PAUSED_AT_CREATION": "False",
            "AIRFLOW__CORE__LOAD_EXAMPLES": "False",
            "AIRFLOW__API__AUTH_BACKEND": "airflow.api.auth.backend.basic_auth",
            "AIRFLOW__WEBSERVER__EXPOSE_CONFIG": "True",
            "AIRFLOW__CORE__FERNET_KEY": "dummy-fernet-key-for-testing-only",
            "AIRFLOW__CORE__DAGS_FOLDER": "/opt/airflow/dags",
            "AIRFLOW__CORE__PLUGINS_FOLDER": "/opt/airflow/plugins",
            "DATA_BUCKET": data_bucket.bucket_name,
            "AIRFLOW__WEBSERVER__RBAC": "False",
            "AWS_DEFAULT_REGION": self.region,
        }

        # Use official Apache Airflow image from Docker Hub
        # The official image already includes AWS CLI via boto3
        airflow_image = ecs.ContainerImage.from_registry("apache/airflow:slim-3.1.6")

        # ALB for Airflow webserver (runs webserver + scheduler in one container)
        webserver_alb = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "AirflowWebserverALB",
            cluster=cluster,
            cpu=1024,
            memory_limit_mib=2048,
            desired_count=1,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=airflow_image,
                container_port=8080,
                environment=airflow_env,
                task_role=airflow_task_role,
                execution_role=airflow_execution_role,
                log_driver=ecs.LogDrivers.aws_logs(
                    stream_prefix="airflow",
                    log_group=airflow_log_group,
                ),
                # Initialize Airflow
                command=[
                    "bash",
                    "-c",
                    """
                    airflow db init || echo "DB already initialized" &&
                    airflow users create --username admin --firstname Admin --lastname User --role Admin --email admin@example.com --password admin 2>/dev/null || echo "User exists" &&
                    airflow dag-processor & airflow scheduler & sleep 5 && exec airflow api-server
                    """,
                ],
            ),
            public_load_balancer=True,
            assign_public_ip=False,
        )

        webserver_alb.target_group.configure_health_check(
            path="/health",
            healthy_http_codes="200",
        )

        # =================================================================
        # Mock External API (Lambda + API Gateway)
        # Phase 1: Lambda-based for fast deployment
        # Phase 2: Can be replaced with ECS Fargate later
        # =================================================================

        # Mock API Lambda function
        mock_api_lambda = lambda_.Function(
            self,
            "MockApiLambda",
            function_name=f"{env_name}-mock-external-api",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambda/mock_api"),
            timeout=Duration.seconds(30),
            memory_size=128,
            environment={
                "INJECT_SCHEMA_CHANGE": "false",
            },
        )

        # API Gateway for mock external API
        mock_api = apigw.LambdaRestApi(
            self,
            "MockExternalApi",
            handler=mock_api_lambda,
            rest_api_name=f"{env_name}-external-api",
            description="Mock external API (simulates third-party data provider)",
            deploy_options=apigw.StageOptions(stage_name="v1"),
        )

        # =================================================================
        # Lambda function for API ingestion
        # =================================================================

        api_ingester_role = iam.Role(
            self,
            "ApiIngesterRole",
            assumed_by=iam.ServicePrincipal("lambda.amazonaws.com"),
            managed_policies=[
                iam.ManagedPolicy.from_aws_managed_policy_name(
                    "service-role/AWSLambdaBasicExecutionRole"
                )
            ],
        )

        # Lambda can write to data bucket
        data_bucket.grant_read_write(api_ingester_role)

        # Lambda can trigger Airflow DAGs via REST API
        api_ingester_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "ec2:DescribeNetworkInterfaces",
                    "ec2:CreateNetworkInterface",
                    "ec2:DeleteNetworkInterface",
                ],
                resources=["*"],
            )
        )

        api_ingester = lambda_.Function(
            self,
            "ApiIngester",
            function_name=f"{env_name}-api-ingester",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambda/api_ingester"),
            role=api_ingester_role,
            timeout=Duration.seconds(60),
            memory_size=256,
            vpc=vpc,
            vpc_subnets=ec2.SubnetSelection(subnet_type=ec2.SubnetType.PRIVATE_WITH_EGRESS),
            security_groups=[airflow_sg],
            environment={
                "DATA_BUCKET": data_bucket.bucket_name,
                "AIRFLOW_WEBSERVER_URL": f"http://{webserver_alb.load_balancer.load_balancer_dns_name}",
                "DAG_ID": "ingest_transform",
                "EXTERNAL_API_URL": mock_api.url,
            },
        )

        # =================================================================
        # Outputs
        # =================================================================

        CfnOutput(
            self,
            "AirflowWebserverUrl",
            value=f"http://{webserver_alb.load_balancer.load_balancer_dns_name}",
            description="Airflow webserver URL",
        )

        CfnOutput(
            self,
            "DagsBucketName",
            value=dags_bucket.bucket_name,
            description="S3 bucket for DAGs",
        )

        CfnOutput(
            self,
            "DataBucketName",
            value=data_bucket.bucket_name,
            description="S3 bucket for test data",
        )

        CfnOutput(
            self,
            "ApiIngesterFunctionName",
            value=api_ingester.function_name,
            description="Lambda function for API ingestion",
        )

        CfnOutput(
            self,
            "VpcId",
            value=vpc.vpc_id,
            description="VPC ID",
        )

        CfnOutput(
            self,
            "MockApiUrl",
            value=mock_api.url,
            description="Mock external API URL (API Gateway + Lambda)",
        )

        CfnOutput(
            self,
            "EcsClusterName",
            value=cluster.cluster_name,
            description="ECS cluster name",
        )

        CfnOutput(
            self,
            "AirflowLogGroupName",
            value=airflow_log_group.log_group_name,
            description="CloudWatch log group for Airflow",
        )
