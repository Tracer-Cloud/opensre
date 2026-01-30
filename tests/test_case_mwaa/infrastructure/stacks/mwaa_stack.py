"""MWAA test case CDK stack.

Creates:
- VPC with public/private subnets (MWAA requirement)
- S3 bucket for DAGs
- S3 bucket for test data
- MWAA environment (mw1.small for cost efficiency)
- Lambda function for API ingestion (Milestone 3+)
- ECS Fargate service with mock external API (Milestone 4)
- API Gateway for stable endpoint (Milestone 4)
- IAM roles with least privilege
"""

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_apigateway as apigw,
    aws_ec2 as ec2,
    aws_ecr_assets as ecr_assets,
    aws_ecs as ecs,
    aws_ecs_patterns as ecs_patterns,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_logs as logs,
    aws_mwaa as mwaa,
    aws_s3 as s3,
    aws_s3_deployment as s3_deploy,
)
from constructs import Construct


class MwaaTestCaseStack(Stack):
    """MWAA test case infrastructure stack."""

    def __init__(self, scope: Construct, construct_id: str, **kwargs) -> None:
        super().__init__(scope, construct_id, **kwargs)

        # Environment name prefix for all resources
        env_name = "tracer-test"

        # VPC for MWAA (required)
        vpc = ec2.Vpc(
            self,
            "MwaaVpc",
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

        # Security group for MWAA
        mwaa_sg = ec2.SecurityGroup(
            self,
            "MwaaSG",
            vpc=vpc,
            description="Security group for MWAA environment",
            allow_all_outbound=True,
        )
        mwaa_sg.add_ingress_rule(
            mwaa_sg,
            ec2.Port.all_traffic(),
            "Allow self-referencing traffic",
        )

        # S3 bucket for DAGs
        dags_bucket = s3.Bucket(
            self,
            "DagsBucket",
            bucket_name=f"{env_name}-dags-{self.account}",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # S3 bucket for test data (Milestone 2+)
        data_bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=f"{env_name}-data-{self.account}",
            versioned=True,
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
        )

        # Deploy DAGs to S3
        s3_deploy.BucketDeployment(
            self,
            "DeployDags",
            sources=[s3_deploy.Source.asset("./dags")],
            destination_bucket=dags_bucket,
            destination_key_prefix="dags",
        )

        # IAM role for MWAA execution
        mwaa_execution_role = iam.Role(
            self,
            "MwaaExecutionRole",
            assumed_by=iam.CompositePrincipal(
                iam.ServicePrincipal("airflow.amazonaws.com"),
                iam.ServicePrincipal("airflow-env.amazonaws.com"),
            ),
        )

        # MWAA execution role policies
        mwaa_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["airflow:PublishMetrics"],
                resources=[f"arn:aws:airflow:{self.region}:{self.account}:environment/{env_name}-env"],
            )
        )

        mwaa_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "s3:GetObject*",
                    "s3:GetBucket*",
                    "s3:List*",
                ],
                resources=[
                    dags_bucket.bucket_arn,
                    f"{dags_bucket.bucket_arn}/*",
                    data_bucket.bucket_arn,
                    f"{data_bucket.bucket_arn}/*",
                ],
            )
        )

        mwaa_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "logs:CreateLogStream",
                    "logs:CreateLogGroup",
                    "logs:PutLogEvents",
                    "logs:GetLogEvents",
                    "logs:GetLogRecord",
                    "logs:GetLogGroupFields",
                    "logs:GetQueryResults",
                ],
                resources=[f"arn:aws:logs:{self.region}:{self.account}:log-group:airflow-{env_name}-env-*"],
            )
        )

        mwaa_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["logs:DescribeLogGroups"],
                resources=["*"],
            )
        )

        mwaa_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "sqs:ChangeMessageVisibility",
                    "sqs:DeleteMessage",
                    "sqs:GetQueueAttributes",
                    "sqs:GetQueueUrl",
                    "sqs:ReceiveMessage",
                    "sqs:SendMessage",
                ],
                resources=[f"arn:aws:sqs:{self.region}:*:airflow-celery-*"],
            )
        )

        mwaa_execution_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=[
                    "kms:Decrypt",
                    "kms:DescribeKey",
                    "kms:GenerateDataKey*",
                    "kms:Encrypt",
                ],
                resources=["*"],
                conditions={
                    "StringLike": {
                        "kms:ViaService": [f"sqs.{self.region}.amazonaws.com"]
                    }
                },
            )
        )

        # MWAA environment
        mwaa_env = mwaa.CfnEnvironment(
            self,
            "MwaaEnvironment",
            name=f"{env_name}-env",
            airflow_version="2.8.1",
            environment_class="mw1.small",
            max_workers=2,
            min_workers=1,
            schedulers=2,
            source_bucket_arn=dags_bucket.bucket_arn,
            dag_s3_path="dags",
            execution_role_arn=mwaa_execution_role.role_arn,
            network_configuration=mwaa.CfnEnvironment.NetworkConfigurationProperty(
                security_group_ids=[mwaa_sg.security_group_id],
                subnet_ids=[
                    subnet.subnet_id
                    for subnet in vpc.private_subnets[:2]
                ],
            ),
            logging_configuration=mwaa.CfnEnvironment.LoggingConfigurationProperty(
                dag_processing_logs=mwaa.CfnEnvironment.ModuleLoggingConfigurationProperty(
                    enabled=True,
                    log_level="INFO",
                ),
                scheduler_logs=mwaa.CfnEnvironment.ModuleLoggingConfigurationProperty(
                    enabled=True,
                    log_level="INFO",
                ),
                task_logs=mwaa.CfnEnvironment.ModuleLoggingConfigurationProperty(
                    enabled=True,
                    log_level="INFO",
                ),
                webserver_logs=mwaa.CfnEnvironment.ModuleLoggingConfigurationProperty(
                    enabled=True,
                    log_level="WARNING",
                ),
                worker_logs=mwaa.CfnEnvironment.ModuleLoggingConfigurationProperty(
                    enabled=True,
                    log_level="INFO",
                ),
            ),
            webserver_access_mode="PUBLIC_ONLY",
        )

        # =================================================================
        # Milestone 4: ECS Fargate with Mock External API + API Gateway
        # =================================================================

        # ECS Cluster
        cluster = ecs.Cluster(
            self,
            "ApiCluster",
            vpc=vpc,
            cluster_name=f"{env_name}-api-cluster",
        )

        # Build Docker image for mock API
        api_image = ecr_assets.DockerImageAsset(
            self,
            "MockApiImage",
            directory="./api",
        )

        # Fargate service with ALB
        fargate_service = ecs_patterns.ApplicationLoadBalancedFargateService(
            self,
            "MockApiService",
            cluster=cluster,
            cpu=256,
            memory_limit_mib=512,
            desired_count=1,
            task_image_options=ecs_patterns.ApplicationLoadBalancedTaskImageOptions(
                image=ecs.ContainerImage.from_docker_image_asset(api_image),
                container_port=8080,
                environment={
                    "INJECT_SCHEMA_CHANGE": "false",
                    "PORT": "8080",
                },
                log_driver=ecs.LogDrivers.aws_logs(
                    stream_prefix="mock-api",
                    log_retention=logs.RetentionDays.ONE_WEEK,
                ),
            ),
            public_load_balancer=True,
            assign_public_ip=False,
        )

        # Configure health check
        fargate_service.target_group.configure_health_check(
            path="/health",
            healthy_http_codes="200",
        )

        # API Gateway to provide stable endpoint
        api = apigw.RestApi(
            self,
            "MockExternalApi",
            rest_api_name=f"{env_name}-external-api",
            description="Mock external API for MWAA test case",
            deploy_options=apigw.StageOptions(
                stage_name="v1",
                logging_level=apigw.MethodLoggingLevel.INFO,
            ),
        )

        # Integration with ALB
        alb_integration = apigw.HttpIntegration(
            f"http://{fargate_service.load_balancer.load_balancer_dns_name}/{{proxy}}",
            http_method="ANY",
            options=apigw.IntegrationOptions(
                request_parameters={
                    "integration.request.path.proxy": "method.request.path.proxy",
                },
            ),
        )

        # Proxy resource for all paths
        proxy_resource = api.root.add_proxy(
            default_integration=alb_integration,
            any_method=True,
        )

        # =================================================================
        # Lambda function for API ingestion (Milestone 3+)
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

        # Lambda can trigger MWAA DAGs
        api_ingester_role.add_to_policy(
            iam.PolicyStatement(
                effect=iam.Effect.ALLOW,
                actions=["airflow:CreateCliToken"],
                resources=[f"arn:aws:airflow:{self.region}:{self.account}:environment/{env_name}-env"],
            )
        )

        api_ingester = lambda_.Function(
            self,
            "ApiIngester",
            function_name=f"{env_name}-api-ingester",
            runtime=lambda_.Runtime.PYTHON_3_11,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("./lambda/api_ingester"),
            role=api_ingester_role,
            timeout=Duration.seconds(60),
            memory_size=256,
            environment={
                "DATA_BUCKET": data_bucket.bucket_name,
                "MWAA_ENVIRONMENT": f"{env_name}-env",
                "DAG_ID": "ingest_transform",
                "EXTERNAL_API_URL": api.url,
            },
        )

        # =================================================================
        # Outputs
        # =================================================================

        CfnOutput(
            self,
            "MwaaEnvironmentName",
            value=f"{env_name}-env",
            description="MWAA environment name",
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
            value=api.url,
            description="Mock external API URL (API Gateway)",
        )

        CfnOutput(
            self,
            "MockApiAlbDns",
            value=fargate_service.load_balancer.load_balancer_dns_name,
            description="Mock external API ALB DNS name",
        )

        CfnOutput(
            self,
            "EcsClusterName",
            value=cluster.cluster_name,
            description="ECS cluster name",
        )
