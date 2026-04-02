#!/usr/bin/env python3
"""Deploy Prefect ECS infrastructure using boto3 SDK.

Creates:
- S3 buckets (landing, processed)
- CloudWatch log group
- ECS cluster with Fargate
- IAM roles (task, execution, trigger lambda)
- Security group for Prefect API
- ECR repositories with Prefect and Alloy images
- ECS task definitions (Prefect server, Flow runner)
- ECS service running Prefect server + Alloy sidecar
- Lambda functions (mock API, trigger)
- API Gateways

Usage:
    python3 tests/e2e/upstream_prefect_ecs_fargate/infrastructure_sdk/deploy.py
"""

import os
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

project_root = Path(__file__).resolve().parents[3]

from app.utils.config import load_env
from tests.shared.infrastructure_sdk import save_outputs
from tests.shared.infrastructure_sdk.resources import (
    api_gateway,
    ecr,
    ecs,
    iam,
    lambda_,
    logs,
    s3,
    vpc,
)

STACK_NAME = "tracer-prefect-ecs"
REGION = "us-east-1"

# Resource names
CLUSTER_NAME = "tracer-prefect-cluster"
LOG_GROUP_NAME = "/ecs/tracer-prefect"
PREFECT_REPO_NAME = "tracer-prefect-ecs/prefect"
ALLOY_REPO_NAME = "tracer-prefect-ecs/alloy"
TASK_ROLE_NAME = "tracer-prefect-ecs-task-role"
EXECUTION_ROLE_NAME = "tracer-prefect-ecs-execution-role"
TRIGGER_LAMBDA_ROLE_NAME = "tracer-prefect-ecs-trigger-lambda-role"
SECURITY_GROUP_NAME = "tracer-prefect-ecs-sg"
PREFECT_SERVICE_NAME = "tracer-prefect-service"
PREFECT_TASK_FAMILY = "tracer-prefect-task"
FLOW_TASK_FAMILY = "tracer-prefect-flow-task"
MOCK_API_LAMBDA_NAME = "tracer-prefect-ecs-mock-api"
TRIGGER_LAMBDA_NAME = "tracer-prefect-ecs-trigger"

# Paths
TESTS_DIR = project_root / "tests"
PREFECT_DOCKERFILE = TESTS_DIR / "upstream_prefect_ecs_fargate" / "infrastructure_code" / "prefect_image" / "Dockerfile"
ALLOY_CONFIG_DIR = TESTS_DIR / "shared" / "infrastructure_code" / "alloy_config"
MOCK_API_CODE = TESTS_DIR / "shared" / "external_vendor_api"
TRIGGER_LAMBDA_CODE = TESTS_DIR / "upstream_prefect_ecs_fargate" / "pipeline_code" / "trigger_lambda"

GRAFANA_ENV_KEYS = [
    "GCLOUD_HOSTED_METRICS_URL",
    "GCLOUD_HOSTED_METRICS_ID",
    "GCLOUD_HOSTED_LOGS_URL",
    "GCLOUD_HOSTED_LOGS_ID",
    "GCLOUD_RW_API_KEY",
    "GCLOUD_OTLP_ENDPOINT",
    "GCLOUD_OTLP_AUTH_HEADER",
]


def _load_grafana_env(env_path: Path) -> dict[str, str]:
    load_env(env_path)
    values = {key: os.getenv(key, "") for key in GRAFANA_ENV_KEYS}
    missing = [key for key, value in values.items() if not value]
    if missing:
        missing_list = ", ".join(missing)
        raise ValueError(f"Missing Grafana env vars in {env_path}: {missing_list}")
    return values


def print_step(step: str) -> None:
    """Print deployment step."""
    print(f"\n{'='*60}\n{step}\n{'='*60}")


def deploy_phase1_foundation() -> dict:
    """Phase 1: Create foundation resources in parallel.

    Creates:
    - VPC lookup
    - S3 buckets
    - IAM roles
    - Log group
    - Security group
    """
    print_step("Phase 1: Foundation Resources")
    results = {}

    with ThreadPoolExecutor(max_workers=6) as executor:
        futures = {
            executor.submit(vpc.get_default_vpc, REGION): "vpc",
            executor.submit(
                s3.create_bucket,
                f"{STACK_NAME}-landing-{int(time.time())}",
                STACK_NAME,
                REGION,
            ): "landing_bucket",
            executor.submit(
                s3.create_bucket,
                f"{STACK_NAME}-processed-{int(time.time())}",
                STACK_NAME,
                REGION,
            ): "processed_bucket",
            executor.submit(iam.create_ecs_task_role, TASK_ROLE_NAME, STACK_NAME, REGION): "task_role",
            executor.submit(iam.create_ecs_execution_role, EXECUTION_ROLE_NAME, STACK_NAME, REGION): "execution_role",
            executor.submit(
                logs.create_log_group, LOG_GROUP_NAME, 7, STACK_NAME, REGION
            ): "log_group",
        }

        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
                print(f"  [OK] {key}")
            except Exception as e:
                print(f"  [FAIL] {key}: {e}")
                raise

    # Get VPC subnets
    vpc_id = results["vpc"]["vpc_id"]
    results["subnets"] = vpc.get_public_subnets(vpc_id, REGION)
    print(f"  [OK] subnets ({len(results['subnets'])} found)")

    # Create security group (needs VPC ID)
    results["security_group"] = vpc.create_security_group(
        name=SECURITY_GROUP_NAME,
        vpc_id=vpc_id,
        description="Security group for Prefect ECS service",
        ingress_rules=[
            {"port": 4200, "cidr": "0.0.0.0/0", "description": "Prefect API"},
        ],
        stack_name=STACK_NAME,
        region=REGION,
    )
    print("  [OK] security_group")

    # Create trigger lambda role
    results["trigger_lambda_role"] = iam.create_lambda_execution_role(
        TRIGGER_LAMBDA_ROLE_NAME, STACK_NAME, REGION
    )
    print("  [OK] trigger_lambda_role")

    return results


def deploy_phase2_images(foundation: dict) -> dict:
    """Phase 2: Create ECR repos and push images.

    Creates:
    - ECR repositories
    - Pushes Prefect and Alloy images
    - ECS cluster
    """
    print_step("Phase 2: Container Images & Cluster")
    results = {}

    # Create ECR repos and cluster in parallel
    with ThreadPoolExecutor(max_workers=3) as executor:
        futures = {
            executor.submit(ecr.create_repository, PREFECT_REPO_NAME, STACK_NAME, REGION): "prefect_repo",
            executor.submit(ecr.create_repository, ALLOY_REPO_NAME, STACK_NAME, REGION): "alloy_repo",
            executor.submit(ecs.create_cluster, CLUSTER_NAME, STACK_NAME, REGION): "cluster",
        }

        for future in as_completed(futures):
            key = futures[future]
            try:
                results[key] = future.result()
                print(f"  [OK] {key}")
            except Exception as e:
                print(f"  [FAIL] {key}: {e}")
                raise

    # Docker login
    print("  Building and pushing images...")
    ecr.docker_login(REGION)

    # Build and push images (must be sequential due to docker)
    # Prefect image - build from tests/ context
    print("  Building Prefect image (ARM64)...")
    prefect_image_uri = _build_and_push_prefect_image(results["prefect_repo"]["uri"])
    results["prefect_image_uri"] = prefect_image_uri
    print(f"  [OK] prefect_image: {prefect_image_uri}")

    # Alloy image
    print("  Building Alloy image (ARM64)...")
    alloy_image_uri = _build_and_push_alloy_image(results["alloy_repo"]["uri"])
    results["alloy_image_uri"] = alloy_image_uri
    print(f"  [OK] alloy_image: {alloy_image_uri}")

    return results


def _build_and_push_prefect_image(repo_uri: str) -> str:
    """Build and push Prefect image using buildx for ARM64."""
    full_uri = f"{repo_uri}:latest"

    # Build with buildx for ARM64
    cmd = [
        "docker", "buildx", "build",
        "--platform", "linux/arm64",
        "-t", full_uri,
        "-f", str(PREFECT_DOCKERFILE),
        "--push",
        str(project_root),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Build stderr: {result.stderr}")
        raise RuntimeError(f"Failed to build Prefect image: {result.stderr}")

    return full_uri


def _build_and_push_alloy_image(repo_uri: str) -> str:
    """Build and push Alloy image using buildx for ARM64."""
    full_uri = f"{repo_uri}:latest"

    # Build with buildx for ARM64
    cmd = [
        "docker", "buildx", "build",
        "--platform", "linux/arm64",
        "-t", full_uri,
        "-f", str(ALLOY_CONFIG_DIR / "Dockerfile"),
        "--push",
        str(ALLOY_CONFIG_DIR),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"Build stderr: {result.stderr}")
        raise RuntimeError(f"Failed to build Alloy image: {result.stderr}")

    return full_uri


def deploy_phase3_ecs(foundation: dict, images: dict) -> dict:
    """Phase 3: Create ECS task definitions and service.

    Creates:
    - Prefect task definition with Alloy sidecar
    - Flow runner task definition
    - ECS service
    """
    print_step("Phase 3: ECS Task Definitions & Service")
    results = {}

    grafana_env = _load_grafana_env(project_root / ".env")

    # Build Prefect container definition
    prefect_container = ecs.build_container_definition(
        name="PrefectContainer",
        image=images["prefect_image_uri"],
        cpu=256,
        memory=1536,
        port_mappings=[{"containerPort": 4200, "protocol": "tcp"}],
        environment={
            "LANDING_BUCKET": foundation["landing_bucket"]["name"],
            "PROCESSED_BUCKET": foundation["processed_bucket"]["name"],
            "PREFECT_API_URL": "http://127.0.0.1:4200/api",
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_SERVICE_NAME": "prefect-etl-pipeline",
            "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_prefect,pipeline.framework=prefect,test_case=test_case_upstream_prefect_ecs_fargate",
            **grafana_env,
        },
        log_group=LOG_GROUP_NAME,
        region=REGION,
        essential=True,
    )

    # Build Alloy sidecar container definition
    alloy_container = ecs.build_container_definition(
        name="AlloySidecar",
        image=images["alloy_image_uri"],
        cpu=128,
        memory=512,
        port_mappings=[
            {"containerPort": 4317, "protocol": "tcp"},
            {"containerPort": 4318, "protocol": "tcp"},
            {"containerPort": 12345, "protocol": "tcp"},
        ],
        environment=grafana_env,
        log_group=LOG_GROUP_NAME,
        region=REGION,
        essential=False,
    )

    # Add container dependency (Prefect depends on Alloy starting first)
    prefect_container["dependsOn"] = [
        {"containerName": "AlloySidecar", "condition": "START"}
    ]

    # Create Prefect task definition
    prefect_task_def = ecs.create_task_definition(
        family=PREFECT_TASK_FAMILY,
        container_definitions=[prefect_container, alloy_container],
        task_role_arn=foundation["task_role"]["arn"],
        execution_role_arn=foundation["execution_role"]["arn"],
        cpu="512",
        memory="2048",
        runtime_platform={"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
        stack_name=STACK_NAME,
        region=REGION,
    )
    results["prefect_task_def"] = prefect_task_def
    print(f"  [OK] prefect_task_def: {prefect_task_def['arn']}")

    # Build Flow container definition
    flow_container = ecs.build_container_definition(
        name="FlowContainer",
        image=images["prefect_image_uri"],
        cpu=256,
        memory=512,
        command=["python", "-m", "prefect_flow.main_pipeline.main_pipeline"],
        environment={
            "LANDING_BUCKET": foundation["landing_bucket"]["name"],
            "PROCESSED_BUCKET": foundation["processed_bucket"]["name"],
            "OTEL_EXPORTER_OTLP_PROTOCOL": "http/protobuf",
            "OTEL_SERVICE_NAME": "prefect-etl-pipeline",
            "OTEL_RESOURCE_ATTRIBUTES": "pipeline.name=upstream_downstream_pipeline_prefect,pipeline.framework=prefect",
            **grafana_env,
        },
        log_group=LOG_GROUP_NAME,
        region=REGION,
        essential=True,
    )

    # Create Flow task definition
    flow_task_def = ecs.create_task_definition(
        family=FLOW_TASK_FAMILY,
        container_definitions=[flow_container],
        task_role_arn=foundation["task_role"]["arn"],
        execution_role_arn=foundation["execution_role"]["arn"],
        cpu="256",
        memory="512",
        runtime_platform={"cpuArchitecture": "ARM64", "operatingSystemFamily": "LINUX"},
        stack_name=STACK_NAME,
        region=REGION,
    )
    results["flow_task_def"] = flow_task_def
    print(f"  [OK] flow_task_def: {flow_task_def['arn']}")

    # Grant task role S3 access
    iam.put_role_policy(
        TASK_ROLE_NAME,
        "S3Access",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{foundation['landing_bucket']['name']}",
                        f"arn:aws:s3:::{foundation['landing_bucket']['name']}/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"],
                    "Resource": [
                        f"arn:aws:s3:::{foundation['processed_bucket']['name']}",
                        f"arn:aws:s3:::{foundation['processed_bucket']['name']}/*",
                    ],
                },
            ],
        },
        REGION,
    )
    print("  [OK] task_role S3 policy")

    # Create ECS service
    service = ecs.create_service(
        cluster=CLUSTER_NAME,
        name=PREFECT_SERVICE_NAME,
        task_definition=prefect_task_def["arn"],
        desired_count=1,
        subnets=foundation["subnets"],
        security_groups=[foundation["security_group"]["group_id"]],
        assign_public_ip=True,
        stack_name=STACK_NAME,
        region=REGION,
    )
    results["service"] = service
    print(f"  [OK] service: {service['arn']}")

    return results


def deploy_phase4_lambdas(foundation: dict, ecs_resources: dict) -> dict:
    """Phase 4: Create Lambda functions and API Gateways.

    Creates:
    - Mock API Lambda + API Gateway
    - Trigger Lambda + API Gateway
    """
    print_step("Phase 4: Lambda Functions & API Gateways")
    results = {}

    # Bundle mock API code
    mock_api_zip = lambda_.bundle_code(MOCK_API_CODE)

    # Create mock API lambda
    mock_api_lambda = lambda_.create_function(
        name=MOCK_API_LAMBDA_NAME,
        role_arn=foundation["trigger_lambda_role"]["arn"],  # Reuse role, it has basic execution
        handler="handler.lambda_handler",
        code_zip=mock_api_zip,
        runtime="python3.11",
        timeout=30,
        memory=128,
        stack_name=STACK_NAME,
        region=REGION,
    )
    results["mock_api_lambda"] = mock_api_lambda
    print(f"  [OK] mock_api_lambda: {mock_api_lambda['arn']}")

    # Create mock API Gateway
    mock_api = api_gateway.create_simple_api_with_lambda(
        api_name="tracer-prefect-mock-api",
        lambda_arn=mock_api_lambda["arn"],
        stack_name=STACK_NAME,
        stage_name="prod",
        region=REGION,
    )
    results["mock_api"] = mock_api
    print(f"  [OK] mock_api: {mock_api['invoke_url']}")

    # Grant trigger lambda role permissions for S3 and ECS
    iam.put_role_policy(
        TRIGGER_LAMBDA_ROLE_NAME,
        "TriggerLambdaPolicy",
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Action": ["s3:PutObject"],
                    "Resource": [
                        f"arn:aws:s3:::{foundation['landing_bucket']['name']}/*",
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["ecs:RunTask"],
                    "Resource": [ecs_resources["flow_task_def"]["arn"]],
                },
                {
                    "Effect": "Allow",
                    "Action": ["ecs:ListTasks", "ecs:DescribeTasks"],
                    "Resource": ["*"],
                },
                {
                    "Effect": "Allow",
                    "Action": ["iam:PassRole"],
                    "Resource": [
                        foundation["task_role"]["arn"],
                        foundation["execution_role"]["arn"],
                    ],
                },
                {
                    "Effect": "Allow",
                    "Action": ["ec2:DescribeNetworkInterfaces"],
                    "Resource": ["*"],
                },
            ],
        },
        REGION,
    )
    print("  [OK] trigger_lambda_role policies")

    # Bundle trigger lambda code with dependencies
    trigger_zip = lambda_.bundle_code(
        TRIGGER_LAMBDA_CODE,
        TRIGGER_LAMBDA_CODE / "requirements.txt",
    )

    # Create trigger lambda
    trigger_lambda = lambda_.create_function(
        name=TRIGGER_LAMBDA_NAME,
        role_arn=foundation["trigger_lambda_role"]["arn"],
        handler="handler.lambda_handler",
        code_zip=trigger_zip,
        runtime="python3.11",
        timeout=60,
        memory=256,
        environment={
            "LANDING_BUCKET": foundation["landing_bucket"]["name"],
            "PROCESSED_BUCKET": foundation["processed_bucket"]["name"],
            "EXTERNAL_API_URL": mock_api["invoke_url"],
            "ECS_CLUSTER": CLUSTER_NAME,
            "TASK_DEFINITION": ecs_resources["flow_task_def"]["arn"],
            "SUBNET_IDS": ",".join(foundation["subnets"]),
            "SECURITY_GROUP_ID": foundation["security_group"]["group_id"],
            "PREFECT_SERVICE_NAME": PREFECT_SERVICE_NAME,
        },
        stack_name=STACK_NAME,
        region=REGION,
    )
    results["trigger_lambda"] = trigger_lambda
    print(f"  [OK] trigger_lambda: {trigger_lambda['arn']}")

    # Create trigger API Gateway
    trigger_api = api_gateway.create_simple_api_with_lambda(
        api_name="tracer-prefect-trigger",
        lambda_arn=trigger_lambda["arn"],
        stack_name=STACK_NAME,
        stage_name="prod",
        region=REGION,
    )
    results["trigger_api"] = trigger_api
    print(f"  [OK] trigger_api: {trigger_api['invoke_url']}")

    return results


def wait_for_service_stable(cluster: str, service: str, timeout_seconds: int = 300) -> bool:
    """Wait for ECS service to reach stable state."""
    print_step("Waiting for ECS Service to Stabilize")

    from tests.shared.infrastructure_sdk.deployer import get_boto3_client

    ecs_client = get_boto3_client("ecs", REGION)

    start_time = time.time()
    while time.time() - start_time < timeout_seconds:
        response = ecs_client.describe_services(cluster=cluster, services=[service])
        if not response["services"]:
            print("  Service not found, waiting...")
            time.sleep(10)
            continue

        svc = response["services"][0]
        running_count = svc.get("runningCount", 0)
        desired_count = svc.get("desiredCount", 1)
        status = svc.get("status", "UNKNOWN")

        elapsed = int(time.time() - start_time)
        print(f"  [{elapsed}s] Status: {status}, Running: {running_count}/{desired_count}")

        if running_count >= desired_count and status == "ACTIVE":
            print("  [OK] Service is stable!")
            return True

        time.sleep(15)

    print("  [WARN] Service did not stabilize within timeout")
    return False


def deploy() -> dict:
    """Deploy all infrastructure and return outputs."""
    start_time = time.time()

    print("=" * 60)
    print(f"Deploying {STACK_NAME}")
    print("=" * 60)

    # Phase 1: Foundation
    foundation = deploy_phase1_foundation()

    # Phase 2: Images & Cluster
    images = deploy_phase2_images(foundation)

    # Phase 3: ECS
    ecs_resources = deploy_phase3_ecs(foundation, images)

    # Phase 4: Lambdas
    lambdas = deploy_phase4_lambdas(foundation, ecs_resources)

    # Wait for service
    wait_for_service_stable(CLUSTER_NAME, PREFECT_SERVICE_NAME)

    # Collect outputs (ensure URLs end with / for compatibility)
    trigger_url = lambdas["trigger_api"]["invoke_url"]
    if not trigger_url.endswith("/"):
        trigger_url += "/"
    mock_url = lambdas["mock_api"]["invoke_url"]
    if not mock_url.endswith("/"):
        mock_url += "/"

    outputs = {
        "LandingBucketName": foundation["landing_bucket"]["name"],
        "ProcessedBucketName": foundation["processed_bucket"]["name"],
        "TriggerApiUrl": trigger_url,
        "MockApiUrl": mock_url,
        "EcsClusterName": CLUSTER_NAME,
        "LogGroupName": LOG_GROUP_NAME,
        "FlowTaskDefinitionArn": ecs_resources["flow_task_def"]["arn"],
        "SecurityGroupId": foundation["security_group"]["group_id"],
        "SubnetIds": ",".join(foundation["subnets"]),
        # Additional outputs for cleanup
        "PrefectTaskDefinitionArn": ecs_resources["prefect_task_def"]["arn"],
        "PrefectRepoUri": images["prefect_image_uri"],
        "AlloyRepoUri": images["alloy_image_uri"],
    }

    # Save outputs
    save_outputs(STACK_NAME, outputs)

    elapsed = int(time.time() - start_time)
    print_step(f"Deployment Complete in {elapsed}s")
    print("\nOutputs:")
    for key, value in outputs.items():
        print(f"  {key}: {value}")

    return outputs


if __name__ == "__main__":
    deploy()
