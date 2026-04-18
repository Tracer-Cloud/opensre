#!/usr/bin/env python3
"""Deploy Flink ECS infrastructure using boto3 SDK.

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

import time
import uuid
from pathlib import Path

project_root = Path(__file__).resolve().parents[3]

from tests.shared.infrastructure_sdk import save_outputs
from tests.shared.infrastructure_sdk.resources import (
    api_gateway,
    ecr,
    ecs,
    iam,
    lambda_,
    logs,
    s3,
    secrets,
    vpc,
)

STACK_NAME = "tracer-flink-ecs"
REGION = "us-east-1"
GRAFANA_SECRET_NAME = "tracer/grafana-cloud"


def deploy() -> dict:
    """Deploy all infrastructure and return outputs."""
    start_time = time.time()
    print(f"Deploying stack: {STACK_NAME}")
    print("=" * 60)

    # Generate unique suffix for S3 buckets
    unique_suffix = uuid.uuid4().hex[:8]

    # ========================================
    # Phase 1: Parallel - VPC, S3, IAM, Logs, Security Group
    # ========================================
    print("\n[Phase 1] Creating VPC, S3, IAM, Logs, Security Group...")

    # VPC lookup
    print("  - Looking up default VPC...")
    vpc_info = vpc.get_default_vpc(REGION)
    vpc_id = vpc_info["vpc_id"]
    subnet_ids = vpc.get_public_subnets(vpc_id, REGION)
    print(f"    VPC: {vpc_id}, Subnets: {subnet_ids}")

    # S3 buckets
    landing_bucket_name = f"tracer-flink-landing-{unique_suffix}"
    processed_bucket_name = f"tracer-flink-processed-{unique_suffix}"

    print(f"  - Creating S3 bucket: {landing_bucket_name}")
    s3.create_bucket(landing_bucket_name, STACK_NAME, REGION)

    print(f"  - Creating S3 bucket: {processed_bucket_name}")
    s3.create_bucket(processed_bucket_name, STACK_NAME, REGION)

    # CloudWatch log group
    log_group_name = "/ecs/tracer-flink"
    print(f"  - Creating log group: {log_group_name}")
    logs.create_log_group(log_group_name, retention_days=7, stack_name=STACK_NAME, region=REGION)

    # IAM roles
    print("  - Creating ECS task role...")
    task_role = iam.create_ecs_task_role(f"{STACK_NAME}-task-role", STACK_NAME, REGION)

    print("  - Creating ECS execution role...")
    execution_role = iam.create_ecs_execution_role(
        f"{STACK_NAME}-execution-role", STACK_NAME, REGION
    )

    print("  - Creating Lambda trigger role...")
    trigger_lambda_role = iam.create_lambda_execution_role(
        f"{STACK_NAME}-trigger-role", STACK_NAME, REGION
    )

    print("  - Creating Mock API Lambda role...")
    mock_api_role = iam.create_lambda_execution_role(
        f"{STACK_NAME}-mock-api-role", STACK_NAME, REGION
    )

    # Security group
    print("  - Creating security group...")
    security_group = vpc.create_security_group(
        name=f"{STACK_NAME}-sg",
        vpc_id=vpc_id,
        description="Security group for Flink ECS tasks",
        stack_name=STACK_NAME,
        region=REGION,
    )

    # ========================================
    # Phase 2: Add IAM permissions
    # ========================================
    print("\n[Phase 2] Configuring IAM permissions...")

    # Task role: S3 access
    print("  - Granting task role S3 read on landing bucket...")
    s3.grant_read(landing_bucket_name, task_role["arn"], REGION)

    print("  - Granting task role S3 read/write on processed bucket...")
    s3.grant_read_write(processed_bucket_name, task_role["arn"], REGION)

    # Execution role: Secrets Manager access for Grafana
    print("  - Granting execution role Secrets Manager access...")
    secret_arn = secrets.get_secret_arn(GRAFANA_SECRET_NAME, REGION)
    iam.put_role_policy(
        execution_role["name"],
        "SecretsAccess",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["secretsmanager:GetSecretValue"],
                    "Resource": [secret_arn],
                }
            ],
        },
        REGION,
    )

    # Trigger Lambda role: S3 write + ECS RunTask (will update task definition ARN later)
    print("  - Granting trigger role S3 write on landing bucket...")
    s3.grant_write(landing_bucket_name, trigger_lambda_role["arn"], REGION)

    # ========================================
    # Phase 3: ECR and ECS Cluster
    # ========================================
    print("\n[Phase 3] Creating ECR repository and ECS cluster...")

    # ECR repository
    ecr_repo_name = f"{STACK_NAME}-flink"
    print(f"  - Creating ECR repository: {ecr_repo_name}")
    ecr_repo = ecr.create_repository(ecr_repo_name, STACK_NAME, REGION)

    # Build and push Docker image
    # Build context is project root to include telemetry packages
    context_dir = project_root
    dockerfile_path = (
        project_root
        / "tests"
        / "upstream_apache_flink_ecs"
        / "infrastructure_code"
        / "flink_image"
        / "Dockerfile"
    )

    print("  - Building and pushing Docker image (ARM64)...")
    print(f"    Dockerfile: {dockerfile_path}")
    print(f"    Context: {context_dir}")

    image_uri = ecr.build_and_push(
        dockerfile_path=dockerfile_path,
        repository_uri=ecr_repo["uri"],
        tag="latest",
        platform="linux/arm64",
        region=REGION,
        context_dir=context_dir,
    )
    print(f"    Image URI: {image_uri}")

    # ECS cluster
    cluster_name = "tracer-flink-cluster"
    print(f"  - Creating ECS cluster: {cluster_name}")
    cluster = ecs.create_cluster(cluster_name, STACK_NAME, REGION)

    # ========================================
    # Phase 4: Task Definition
    # ========================================
    print("\n[Phase 4] Creating ECS task definition...")

    # Build container definitions
    flink_container = ecs.build_container_definition(
        name="FlinkContainer",
        image=image_uri,
        cpu=256,
        memory=1536,
        environment={
            "LANDING_BUCKET": landing_bucket_name,
            "PROCESSED_BUCKET": processed_bucket_name,
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_SERVICE_NAME": "flink-etl-pipeline",
            "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_flink,pipeline.framework=flink,test_case=e2e_upstream_apache_flink_ecs",
        },
        secrets=[
            {
                "name": "GCLOUD_HOSTED_METRICS_URL",
                "valueFrom": f"{secret_arn}:GCLOUD_HOSTED_METRICS_URL::",
            },
            {
                "name": "GCLOUD_HOSTED_METRICS_ID",
                "valueFrom": f"{secret_arn}:GCLOUD_HOSTED_METRICS_ID::",
            },
            {
                "name": "GCLOUD_HOSTED_LOGS_URL",
                "valueFrom": f"{secret_arn}:GCLOUD_HOSTED_LOGS_URL::",
            },
            {"name": "GCLOUD_HOSTED_LOGS_ID", "valueFrom": f"{secret_arn}:GCLOUD_HOSTED_LOGS_ID::"},
            {"name": "GCLOUD_RW_API_KEY", "valueFrom": f"{secret_arn}:GCLOUD_RW_API_KEY::"},
            {"name": "GCLOUD_OTLP_ENDPOINT", "valueFrom": f"{secret_arn}:GCLOUD_OTLP_ENDPOINT::"},
            {
                "name": "GCLOUD_OTLP_AUTH_HEADER",
                "valueFrom": f"{secret_arn}:GCLOUD_OTLP_AUTH_HEADER::",
            },
        ],
        log_group=log_group_name,
        region=REGION,
        essential=True,
    )

    # Alloy sidecar for OTEL
    alloy_container = ecs.build_container_definition(
        name="alloy",
        image="grafana/alloy:v1.4.2",
        cpu=128,
        memory=256,
        command=[
            "run",
            "--server.http.listen-addr=0.0.0.0:12345",
            "/etc/alloy/config.alloy",
        ],
        secrets=[
            {
                "name": "GCLOUD_OTLP_ENDPOINT",
                "valueFrom": f"{secret_arn}:GCLOUD_OTLP_ENDPOINT::",
            },
            {
                "name": "GCLOUD_OTLP_AUTH_HEADER",
                "valueFrom": f"{secret_arn}:GCLOUD_OTLP_AUTH_HEADER::",
            },
        ],
        log_group=log_group_name,
        region=REGION,
        essential=False,
    )

    # Add Alloy config file via environment or mount
    # For simplicity, we use the embedded config approach
    alloy_config = """
otelcol.receiver.otlp "default" {
  grpc {
    endpoint = "0.0.0.0:4317"
  }
  http {
    endpoint = "0.0.0.0:4318"
  }
  output {
    traces = [otelcol.exporter.otlphttp.grafana.input]
  }
}

otelcol.exporter.otlphttp "grafana" {
  client {
    endpoint = env("GCLOUD_OTLP_ENDPOINT")
    headers = {
      "Authorization" = env("GCLOUD_OTLP_AUTH_HEADER"),
    }
  }
}
"""

    # Update alloy container with config
    alloy_container.setdefault("environment", []).append(
        {"name": "ALLOY_CONFIG", "value": alloy_config.strip()}
    )

    # Add container dependency
    flink_container["dependsOn"] = [
        {"containerName": "alloy", "condition": "START"},
    ]

    # Create task definition
    task_def = ecs.create_task_definition(
        family=f"{STACK_NAME}-task",
        container_definitions=[flink_container, alloy_container],
        task_role_arn=task_role["arn"],
        execution_role_arn=execution_role["arn"],
        cpu="512",
        memory="2048",
        runtime_platform={"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"    Task Definition: {task_def['arn']}")

    # ========================================
    # Phase 5: Update Trigger Lambda Role with Task Definition ARN
    # ========================================
    print("\n[Phase 5] Updating Lambda role with ECS RunTask permissions...")

    iam.put_role_policy(
        trigger_lambda_role["name"],
        "EcsRunTask",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["ecs:RunTask"],
                    "Resource": [f"{task_def['arn'].rsplit(':', 1)[0]}:*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["iam:PassRole"],
                    "Resource": [task_role["arn"], execution_role["arn"]],
                },
            ],
        },
        REGION,
    )

    # ========================================
    # Phase 6: Lambda Functions and API Gateways
    # ========================================
    print("\n[Phase 6] Creating Lambda functions and API Gateways...")

    # Mock API Lambda
    mock_api_code_dir = project_root / "tests/shared/external_vendor_api"
    print(f"  - Bundling Mock API Lambda from: {mock_api_code_dir}")
    mock_api_code = lambda_.bundle_code(mock_api_code_dir)

    mock_api_lambda_name = f"{STACK_NAME}-mock-api"
    print(f"  - Creating Mock API Lambda: {mock_api_lambda_name}")

    mock_api_lambda = lambda_.create_function(
        name=mock_api_lambda_name,
        role_arn=mock_api_role["arn"],
        handler="handler.lambda_handler",
        code_zip=mock_api_code,
        runtime="python3.11",
        timeout=30,
        memory=128,
        stack_name=STACK_NAME,
        region=REGION,
    )

    # Create Mock API Gateway
    print("  - Creating Mock API Gateway...")
    mock_api = api_gateway.create_simple_api_with_lambda(
        api_name=f"{STACK_NAME}-mock-api",
        lambda_arn=mock_api_lambda["arn"],
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"    Mock API URL: {mock_api['invoke_url']}")

    # Trigger Lambda
    trigger_code_dir = (
        project_root / "tests/e2e/upstream_apache_flink_ecs/pipeline_code/trigger_lambda"
    )
    requirements_file = trigger_code_dir / "requirements.txt"
    print(f"  - Bundling Trigger Lambda from: {trigger_code_dir}")
    trigger_code = lambda_.bundle_code(trigger_code_dir, requirements_file)

    trigger_lambda_name = f"{STACK_NAME}-trigger"
    print(f"  - Creating Trigger Lambda: {trigger_lambda_name}")

    trigger_lambda = lambda_.create_function(
        name=trigger_lambda_name,
        role_arn=trigger_lambda_role["arn"],
        handler="handler.lambda_handler",
        code_zip=trigger_code,
        runtime="python3.11",
        timeout=60,
        memory=256,
        environment={
            "LANDING_BUCKET": landing_bucket_name,
            "PROCESSED_BUCKET": processed_bucket_name,
            "EXTERNAL_API_URL": mock_api["invoke_url"],
            "ECS_CLUSTER": cluster["arn"],
            "TASK_DEFINITION": task_def["arn"],
            "SUBNET_IDS": ",".join(subnet_ids),
            "SECURITY_GROUP_ID": security_group["group_id"],
        },
        stack_name=STACK_NAME,
        region=REGION,
    )

    # Create Trigger API Gateway
    print("  - Creating Trigger API Gateway...")
    trigger_api = api_gateway.create_simple_api_with_lambda(
        api_name=f"{STACK_NAME}-trigger",
        lambda_arn=trigger_lambda["arn"],
        stack_name=STACK_NAME,
        region=REGION,
    )
    print(f"    Trigger API URL: {trigger_api['invoke_url']}")

    # ========================================
    # Save Outputs
    # ========================================
    outputs = {
        "LandingBucketName": landing_bucket_name,
        "ProcessedBucketName": processed_bucket_name,
        "TriggerApiUrl": trigger_api["invoke_url"],
        "MockApiUrl": mock_api["invoke_url"],
        "EcsClusterName": cluster_name,
        "EcsClusterArn": cluster["arn"],
        "LogGroupName": log_group_name,
        "TaskDefinitionArn": task_def["arn"],
        "SecurityGroupId": security_group["group_id"],
        "SubnetIds": ",".join(subnet_ids),
        "TriggerLambdaName": trigger_lambda_name,
        "TriggerLambdaArn": trigger_lambda["arn"],
        "MockApiLambdaName": mock_api_lambda_name,
        "MockApiLambdaArn": mock_api_lambda["arn"],
        "VpcId": vpc_id,
        "EcrRepositoryUri": ecr_repo["uri"],
        "ImageUri": image_uri,
    }

    save_outputs(STACK_NAME, outputs)

    elapsed = time.time() - start_time
    print("\n" + "=" * 60)
    print(f"Deployment completed in {elapsed:.1f}s")
    print("=" * 60)
    print("\nOutputs:")
    for key, value in outputs.items():
        print(f"  {key}: {value}")

    return outputs


if __name__ == "__main__":
    deploy()
