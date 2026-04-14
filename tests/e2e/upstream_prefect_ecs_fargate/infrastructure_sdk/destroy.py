#!/usr/bin/env python3
"""Destroy Prefect ECS infrastructure.

Removes all resources created by deploy.py in the correct dependency order.

Usage:
    python3 tests/e2e/upstream_prefect_ecs_fargate/infrastructure_sdk/destroy.py
"""

import time

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk import destroy_stack, load_outputs
from tests.shared.infrastructure_sdk.config import delete_outputs
from tests.shared.infrastructure_sdk.deployer import get_boto3_client
from tests.shared.infrastructure_sdk.resources import ecr, ecs, iam, lambda_, logs, s3, vpc
from tests.shared.infrastructure_sdk.resources.api_gateway import delete_api

STACK_NAME = "tracer-prefect-ecs"
REGION = "us-east-1"

# Resource names (must match deploy.py)
CLUSTER_NAME = "tracer-prefect-cluster"
LOG_GROUP_NAME = "/ecs/tracer-prefect"
PREFECT_REPO_NAME = "tracer-prefect-ecs/prefect"
ALLOY_REPO_NAME = "tracer-prefect-ecs/alloy"
TASK_ROLE_NAME = "tracer-prefect-ecs-task-role"
EXECUTION_ROLE_NAME = "tracer-prefect-ecs-execution-role"
TRIGGER_LAMBDA_ROLE_NAME = "tracer-prefect-ecs-trigger-lambda-role"
SECURITY_GROUP_NAME = "tracer-prefect-ecs-sg"
PREFECT_SERVICE_NAME = "tracer-prefect-service"
MOCK_API_LAMBDA_NAME = "tracer-prefect-ecs-mock-api"
TRIGGER_LAMBDA_NAME = "tracer-prefect-ecs-trigger"


def print_step(step: str) -> None:
    """Print destruction step."""
    print(f"\n{'='*60}\n{step}\n{'='*60}")


def delete_ecs_service() -> None:
    """Delete ECS service (scale to 0 first)."""
    print("  Deleting ECS service...")
    try:
        ecs.delete_service(CLUSTER_NAME, PREFECT_SERVICE_NAME, REGION)
        print("  [OK] ECS service deleted")
    except ClientError as e:
        if "ServiceNotFoundException" not in str(e):
            print(f"  [WARN] Could not delete service: {e}")


def stop_running_tasks() -> None:
    """Stop any running tasks in the cluster."""
    print("  Stopping running tasks...")
    ecs_client = get_boto3_client("ecs", REGION)

    try:
        # List all running tasks
        response = ecs_client.list_tasks(cluster=CLUSTER_NAME, desiredStatus="RUNNING")
        task_arns = response.get("taskArns", [])

        for task_arn in task_arns:
            try:
                ecs_client.stop_task(cluster=CLUSTER_NAME, task=task_arn, reason="Stack destruction")
                print(f"  [OK] Stopped task: {task_arn.split('/')[-1]}")
            except ClientError:
                # Task may have already stopped between list and stop calls
                continue

        # Wait for tasks to stop
        if task_arns:
            print("  Waiting for tasks to stop...")
            time.sleep(10)

    except ClientError as e:
        print(f"  [WARN] Could not stop tasks: {e}")


def delete_api_gateways() -> None:
    """Delete API Gateways."""
    print("  Deleting API Gateways...")
    api_client = get_boto3_client("apigateway", REGION)

    try:
        # Find APIs by name
        response = api_client.get_rest_apis(limit=100)
        for api in response.get("items", []):
            if api["name"] in ["tracer-prefect-mock-api", "tracer-prefect-trigger"]:
                try:
                    delete_api(api["id"], REGION)
                    print(f"  [OK] Deleted API: {api['name']}")
                except ClientError as e:
                    print(f"  [WARN] Could not delete API {api['name']}: {e}")
    except ClientError as e:
        print(f"  [WARN] Could not list APIs: {e}")


def delete_lambda_functions() -> None:
    """Delete Lambda functions."""
    print("  Deleting Lambda functions...")

    for func_name in [MOCK_API_LAMBDA_NAME, TRIGGER_LAMBDA_NAME]:
        try:
            lambda_.delete_function(func_name, REGION)
            print(f"  [OK] Deleted Lambda: {func_name}")
        except ClientError as e:
            if "ResourceNotFoundException" not in str(e):
                print(f"  [WARN] Could not delete Lambda {func_name}: {e}")


def delete_ecs_cluster() -> None:
    """Delete ECS cluster."""
    print("  Deleting ECS cluster...")
    try:
        ecs.delete_cluster(CLUSTER_NAME, REGION)
        print("  [OK] ECS cluster deleted")
    except ClientError as e:
        if "ClusterNotFoundException" not in str(e):
            print(f"  [WARN] Could not delete cluster: {e}")


def delete_ecr_repositories() -> None:
    """Delete ECR repositories and images."""
    print("  Deleting ECR repositories...")

    for repo_name in [PREFECT_REPO_NAME, ALLOY_REPO_NAME]:
        try:
            ecr.delete_repository(repo_name, REGION)
            print(f"  [OK] Deleted ECR repo: {repo_name}")
        except ClientError as e:
            if "RepositoryNotFoundException" not in str(e):
                print(f"  [WARN] Could not delete ECR repo {repo_name}: {e}")


def delete_iam_roles() -> None:
    """Delete IAM roles and policies."""
    print("  Deleting IAM roles...")

    for role_name in [TASK_ROLE_NAME, EXECUTION_ROLE_NAME, TRIGGER_LAMBDA_ROLE_NAME]:
        try:
            iam.delete_role(role_name, REGION)
            print(f"  [OK] Deleted IAM role: {role_name}")
        except ClientError as e:
            if "NoSuchEntity" not in str(e):
                print(f"  [WARN] Could not delete role {role_name}: {e}")


def delete_s3_buckets() -> None:
    """Delete S3 buckets."""
    print("  Deleting S3 buckets...")

    try:
        outputs = load_outputs(STACK_NAME)
        landing_bucket = outputs.get("LandingBucketName")
        processed_bucket = outputs.get("ProcessedBucketName")

        if landing_bucket:
            try:
                s3.delete_bucket(landing_bucket, REGION)
                print(f"  [OK] Deleted S3 bucket: {landing_bucket}")
            except ClientError as e:
                print(f"  [WARN] Could not delete bucket {landing_bucket}: {e}")

        if processed_bucket:
            try:
                s3.delete_bucket(processed_bucket, REGION)
                print(f"  [OK] Deleted S3 bucket: {processed_bucket}")
            except ClientError as e:
                print(f"  [WARN] Could not delete bucket {processed_bucket}: {e}")

    except FileNotFoundError:
        print("  [WARN] No outputs file found, skipping S3 bucket deletion")
        # Try to find buckets by prefix
        _delete_buckets_by_prefix()


def _delete_buckets_by_prefix() -> None:
    """Find and delete buckets matching our stack pattern."""
    s3_client = get_boto3_client("s3", REGION)

    try:
        response = s3_client.list_buckets()
        for bucket in response.get("Buckets", []):
            name = bucket["Name"]
            if name.startswith(f"{STACK_NAME}-landing-") or name.startswith(f"{STACK_NAME}-processed-"):
                try:
                    s3.delete_bucket(name, REGION)
                    print(f"  [OK] Deleted S3 bucket: {name}")
                except ClientError as e:
                    print(f"  [WARN] Could not delete bucket {name}: {e}")
    except ClientError as e:
        print(f"  [WARN] Could not list buckets: {e}")


def delete_log_group() -> None:
    """Delete CloudWatch log group."""
    print("  Deleting CloudWatch log group...")
    try:
        logs.delete_log_group(LOG_GROUP_NAME, REGION)
        print(f"  [OK] Deleted log group: {LOG_GROUP_NAME}")
    except ClientError as e:
        if "ResourceNotFoundException" not in str(e):
            print(f"  [WARN] Could not delete log group: {e}")


def delete_security_group() -> None:
    """Delete security group."""
    print("  Deleting security group...")

    # Find security group by name
    ec2_client = get_boto3_client("ec2", REGION)

    try:
        response = ec2_client.describe_security_groups(
            Filters=[{"Name": "group-name", "Values": [SECURITY_GROUP_NAME]}]
        )

        for sg in response.get("SecurityGroups", []):
            try:
                vpc.delete_security_group(sg["GroupId"], REGION)
                print(f"  [OK] Deleted security group: {sg['GroupId']}")
            except ClientError as e:
                print(f"  [WARN] Could not delete security group: {e}")

    except ClientError as e:
        print(f"  [WARN] Could not find security group: {e}")


def deregister_task_definitions() -> None:
    """Deregister task definitions."""
    print("  Deregistering task definitions...")
    ecs_client = get_boto3_client("ecs", REGION)

    for family in ["tracer-prefect-task", "tracer-prefect-flow-task"]:
        try:
            # List all revisions
            response = ecs_client.list_task_definitions(familyPrefix=family, status="ACTIVE")
            for task_def_arn in response.get("taskDefinitionArns", []):
                try:
                    ecs.deregister_task_definition(task_def_arn, REGION)
                    print(f"  [OK] Deregistered: {task_def_arn.split('/')[-1]}")
                except ClientError:
                    # Task definition may be already deregistered or in use; skip and continue
                    continue
        except ClientError as e:
            print(f"  [WARN] Could not list task definitions for {family}: {e}")


def destroy() -> dict:
    """Destroy all infrastructure."""
    start_time = time.time()

    print("=" * 60)
    print(f"Destroying {STACK_NAME}")
    print("=" * 60)

    results = {"deleted": [], "failed": []}

    # Phase 1: Stop active workloads
    print_step("Phase 1: Stop Active Workloads")
    delete_ecs_service()
    stop_running_tasks()

    # Wait for tasks to fully stop
    print("  Waiting for tasks to terminate...")
    time.sleep(15)

    # Phase 2: Delete compute resources
    print_step("Phase 2: Delete Compute Resources")
    delete_api_gateways()
    delete_lambda_functions()
    delete_ecs_cluster()
    deregister_task_definitions()

    # Phase 3: Delete storage and images
    print_step("Phase 3: Delete Storage & Images")
    delete_ecr_repositories()
    delete_s3_buckets()
    delete_log_group()

    # Phase 4: Delete IAM and networking
    print_step("Phase 4: Delete IAM & Networking")
    delete_iam_roles()
    delete_security_group()

    # Delete outputs file
    print_step("Cleanup")
    try:
        delete_outputs(STACK_NAME)
        print("  [OK] Deleted outputs file")
    except Exception as e:
        # Outputs file may not exist if deployment was incomplete
        print(f"  [WARN] Could not delete outputs file: {e}")

    # Also run tag-based cleanup for any missed resources
    print("  Running tag-based cleanup...")
    try:
        tag_results = destroy_stack(STACK_NAME, REGION)
        if tag_results["deleted"]:
            print(f"  [OK] Cleaned up {len(tag_results['deleted'])} additional tagged resources")
    except Exception as e:
        print(f"  [WARN] Tag-based cleanup: {e}")

    elapsed = int(time.time() - start_time)
    print_step(f"Destruction Complete in {elapsed}s")

    return results


if __name__ == "__main__":
    destroy()
