"""ECS cluster, task definition, service creation."""

import time
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags_ecs,
)


def create_cluster(name: str, stack_name: str, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Create ECS cluster.

    Args:
        name: Cluster name.
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with cluster info: arn, name.
    """
    ecs_client = get_boto3_client("ecs", region)

    # First, check if cluster already exists and is ACTIVE
    try:
        response = ecs_client.describe_clusters(clusters=[name])
        if response.get("clusters"):
            cluster = response["clusters"][0]
            if cluster.get("status") == "ACTIVE":
                return {
                    "arn": cluster["clusterArn"],
                    "name": name,
                }
            # If INACTIVE, wait for it to be fully deleted
            if cluster.get("status") == "INACTIVE":
                time.sleep(5)  # Give time for cleanup
    except ClientError:
        # Cluster doesn't exist yet; proceed to create
        pass

    # Create new cluster
    try:
        response = ecs_client.create_cluster(
            clusterName=name,
            tags=get_standard_tags_ecs(stack_name),
            capacityProviders=["FARGATE", "FARGATE_SPOT"],
            defaultCapacityProviderStrategy=[
                {"capacityProvider": "FARGATE", "weight": 1},
            ],
        )
        cluster_arn = response["cluster"]["clusterArn"]
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        # Handle cluster already exists or idempotent request conflict
        if "ClusterAlreadyExists" in str(e) or error_code == "InvalidParameterException":
            # Get existing cluster ARN - it might have become active
            response = ecs_client.describe_clusters(clusters=[name])
            if response.get("clusters") and response["clusters"][0].get("status") == "ACTIVE":
                cluster_arn = response["clusters"][0]["clusterArn"]
            else:
                raise
        else:
            raise

    return {
        "arn": cluster_arn,
        "name": name,
    }


def create_task_definition(
    family: str,
    container_definitions: list[dict[str, Any]],
    task_role_arn: str,
    execution_role_arn: str,
    cpu: str = "256",
    memory: str = "512",
    runtime_platform: dict[str, str] | None = None,
    stack_name: str | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Register task definition.

    Args:
        family: Task definition family name.
        container_definitions: List of container definitions.
        task_role_arn: ARN of the task role.
        execution_role_arn: ARN of the execution role.
        cpu: CPU units (256, 512, 1024, 2048, 4096).
        memory: Memory in MB (512, 1024, 2048, etc.).
        runtime_platform: Platform config (e.g., {"cpuArchitecture": "ARM64"}).
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with task definition info: arn, family, revision.
    """
    ecs_client = get_boto3_client("ecs", region)

    config: dict[str, Any] = {
        "family": family,
        "containerDefinitions": container_definitions,
        "taskRoleArn": task_role_arn,
        "executionRoleArn": execution_role_arn,
        "cpu": cpu,
        "memory": memory,
        "networkMode": "awsvpc",
        "requiresCompatibilities": ["FARGATE"],
    }

    if runtime_platform:
        config["runtimePlatform"] = {
            "operatingSystemFamily": runtime_platform.get("operatingSystemFamily", "LINUX"),
            "cpuArchitecture": runtime_platform.get("cpuArchitecture", "X86_64"),
        }

    if stack_name:
        config["tags"] = get_standard_tags_ecs(stack_name)

    response = ecs_client.register_task_definition(**config)

    task_def = response["taskDefinition"]
    return {
        "arn": task_def["taskDefinitionArn"],
        "family": task_def["family"],
        "revision": task_def["revision"],
    }


def create_service(
    cluster: str,
    name: str,
    task_definition: str,
    desired_count: int = 1,
    subnets: list[str] | None = None,
    security_groups: list[str] | None = None,
    assign_public_ip: bool = True,
    stack_name: str | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create Fargate service.

    Args:
        cluster: Cluster name or ARN.
        name: Service name.
        task_definition: Task definition family:revision or ARN.
        desired_count: Number of tasks to run.
        subnets: List of subnet IDs.
        security_groups: List of security group IDs.
        assign_public_ip: Whether to assign public IP.
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with service info: arn, name.
    """
    from tests.shared.infrastructure_sdk.resources.vpc import get_default_vpc, get_public_subnets

    ecs_client = get_boto3_client("ecs", region)

    # Get default VPC resources if not provided
    if not subnets:
        vpc = get_default_vpc(region)
        subnets = get_public_subnets(vpc["vpc_id"], region)

    network_config: dict[str, Any] = {
        "awsvpcConfiguration": {
            "subnets": subnets,
            "assignPublicIp": "ENABLED" if assign_public_ip else "DISABLED",
        }
    }

    if security_groups:
        network_config["awsvpcConfiguration"]["securityGroups"] = security_groups

    config: dict[str, Any] = {
        "cluster": cluster,
        "serviceName": name,
        "taskDefinition": task_definition,
        "desiredCount": desired_count,
        "launchType": "FARGATE",
        "networkConfiguration": network_config,
    }

    if stack_name:
        config["tags"] = get_standard_tags_ecs(stack_name)
        config["propagateTags"] = "SERVICE"

    try:
        response = ecs_client.create_service(**config)
        service_arn = response["service"]["serviceArn"]
    except ClientError as e:
        if (
            "ServiceAlreadyExists" in str(e)
            or e.response["Error"]["Code"] == "InvalidParameterException"
        ):
            # Update existing service
            ecs_client.update_service(
                cluster=cluster,
                service=name,
                taskDefinition=task_definition,
                desiredCount=desired_count,
            )
            response = ecs_client.describe_services(cluster=cluster, services=[name])
            service_arn = response["services"][0]["serviceArn"]
        else:
            raise

    return {
        "arn": service_arn,
        "name": name,
    }


def run_task(
    cluster: str,
    task_definition: str,
    subnets: list[str],
    security_groups: list[str] | None = None,
    assign_public_ip: bool = True,
    overrides: dict[str, Any] | None = None,
    count: int = 1,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Run one-shot ECS task.

    Args:
        cluster: Cluster name or ARN.
        task_definition: Task definition family:revision or ARN.
        subnets: List of subnet IDs.
        security_groups: List of security group IDs.
        assign_public_ip: Whether to assign public IP.
        overrides: Task overrides (environment, command, etc.).
        count: Number of tasks to run.
        region: AWS region.

    Returns:
        Dictionary with task info: task_arns, cluster.
    """
    ecs_client = get_boto3_client("ecs", region)

    network_config: dict[str, Any] = {
        "awsvpcConfiguration": {
            "subnets": subnets,
            "assignPublicIp": "ENABLED" if assign_public_ip else "DISABLED",
        }
    }

    if security_groups:
        network_config["awsvpcConfiguration"]["securityGroups"] = security_groups

    config: dict[str, Any] = {
        "cluster": cluster,
        "taskDefinition": task_definition,
        "launchType": "FARGATE",
        "networkConfiguration": network_config,
        "count": count,
    }

    if overrides:
        config["overrides"] = overrides

    response = ecs_client.run_task(**config)

    return {
        "task_arns": [t["taskArn"] for t in response.get("tasks", [])],
        "cluster": cluster,
        "failures": response.get("failures", []),
    }


def wait_for_tasks_stopped(
    cluster: str,
    task_arns: list[str],
    region: str = DEFAULT_REGION,
    timeout_seconds: int = 600,
) -> list[dict[str, Any]]:
    """Wait for tasks to stop and return their exit codes.

    Args:
        cluster: Cluster name or ARN.
        task_arns: List of task ARNs.
        region: AWS region.
        timeout_seconds: Maximum time to wait.

    Returns:
        List of task details with exit codes.
    """
    ecs_client = get_boto3_client("ecs", region)

    waiter = ecs_client.get_waiter("tasks_stopped")
    waiter.wait(
        cluster=cluster,
        tasks=task_arns,
        WaiterConfig={
            "Delay": 10,
            "MaxAttempts": timeout_seconds // 10,
        },
    )

    response = ecs_client.describe_tasks(cluster=cluster, tasks=task_arns)

    results = []
    for task in response.get("tasks", []):
        container_results = []
        for container in task.get("containers", []):
            container_results.append(
                {
                    "name": container["name"],
                    "exit_code": container.get("exitCode"),
                    "reason": container.get("reason"),
                }
            )
        results.append(
            {
                "task_arn": task["taskArn"],
                "last_status": task["lastStatus"],
                "stopped_reason": task.get("stoppedReason"),
                "containers": container_results,
            }
        )

    return results


def delete_cluster(name: str, region: str = DEFAULT_REGION) -> None:
    """Delete cluster (must have no services).

    Args:
        name: Cluster name.
        region: AWS region.
    """
    ecs_client = get_boto3_client("ecs", region)

    try:
        ecs_client.delete_cluster(cluster=name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ClusterNotFoundException":
            raise


def delete_service(cluster: str, service: str, region: str = DEFAULT_REGION) -> None:
    """Delete service.

    Args:
        cluster: Cluster name or ARN.
        service: Service name or ARN.
        region: AWS region.
    """
    ecs_client = get_boto3_client("ecs", region)

    try:
        # Scale to 0 first
        ecs_client.update_service(cluster=cluster, service=service, desiredCount=0)
        time.sleep(5)
    except ClientError:
        # Service may already be at 0 or not found; proceed with deletion
        pass

    try:
        ecs_client.delete_service(cluster=cluster, service=service, force=True)
    except ClientError as e:
        if e.response["Error"]["Code"] != "ServiceNotFoundException":
            raise


def get_cluster(name: str, region: str = DEFAULT_REGION) -> dict[str, Any] | None:
    """Get cluster details.

    Args:
        name: Cluster name.
        region: AWS region.

    Returns:
        Cluster details or None if not found.
    """
    ecs_client = get_boto3_client("ecs", region)

    try:
        response = ecs_client.describe_clusters(clusters=[name])
        if response["clusters"]:
            cluster = response["clusters"][0]
            return {
                "arn": cluster["clusterArn"],
                "name": cluster["clusterName"],
                "status": cluster["status"],
            }
        return None
    except ClientError:
        return None


def deregister_task_definition(task_definition: str, region: str = DEFAULT_REGION) -> None:
    """Deregister a task definition.

    Args:
        task_definition: Task definition ARN or family:revision.
        region: AWS region.
    """
    ecs_client = get_boto3_client("ecs", region)

    try:
        ecs_client.deregister_task_definition(taskDefinition=task_definition)
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidParameterException":
            raise


def build_container_definition(
    name: str,
    image: str,
    cpu: int = 256,
    memory: int = 512,
    port_mappings: list[dict[str, Any]] | None = None,
    environment: dict[str, str] | None = None,
    secrets: list[dict[str, str]] | None = None,
    command: list[str] | None = None,
    entry_point: list[str] | None = None,
    log_group: str | None = None,
    region: str = DEFAULT_REGION,
    essential: bool = True,
) -> dict[str, Any]:
    """Build a container definition dict.

    Args:
        name: Container name.
        image: Docker image URI.
        cpu: CPU units for this container.
        memory: Memory limit in MB.
        port_mappings: List of port mappings.
        environment: Environment variables dict.
        secrets: List of secrets (name, valueFrom ARN).
        command: Container command override.
        entry_point: Container entry point override.
        log_group: CloudWatch log group name.
        region: AWS region.
        essential: Whether container is essential.

    Returns:
        Container definition dict ready for task definition.
    """
    container: dict[str, Any] = {
        "name": name,
        "image": image,
        "cpu": cpu,
        "memory": memory,
        "essential": essential,
    }

    if port_mappings:
        container["portMappings"] = port_mappings

    if environment:
        container["environment"] = [{"name": k, "value": v} for k, v in environment.items()]

    if secrets:
        container["secrets"] = secrets

    if command:
        container["command"] = command

    if entry_point:
        container["entryPoint"] = entry_point

    if log_group:
        container["logConfiguration"] = {
            "logDriver": "awslogs",
            "options": {
                "awslogs-group": log_group,
                "awslogs-region": region,
                "awslogs-stream-prefix": name,
            },
        }

    return container
