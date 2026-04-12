"""Tag-based resource cleanup."""

import time
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import get_boto3_client

# Resource types and their deletion order (dependencies matter)
RESOURCE_DELETION_ORDER = [
    "elasticloadbalancing:loadbalancer",
    "elasticloadbalancing:targetgroup",
    "ecs:service",
    "ecs:task",
    "ecs:cluster",
    "apigateway:restapis",
    "lambda:function",
    "iam:role",
    "s3:bucket",
    "logs:log-group",
    "ecr:repository",
    "ec2:security-group",
]


def find_resources_by_stack(stack_name: str, region: str = "us-east-1") -> dict[str, list[str]]:
    """Find all resources tagged with stack name using Resource Groups Tagging API.

    Args:
        stack_name: The stack name to search for.
        region: AWS region.

    Returns:
        Dictionary mapping resource type to list of resource ARNs.
    """
    client = get_boto3_client("resourcegroupstaggingapi", region)
    resources: dict[str, list[str]] = {}

    paginator = client.get_paginator("get_resources")
    for page in paginator.paginate(
        TagFilters=[
            {"Key": "tracer:stack", "Values": [stack_name]},
            {"Key": "tracer:managed", "Values": ["sdk"]},
        ]
    ):
        for resource in page.get("ResourceTagMappingList", []):
            arn = resource["ResourceARN"]
            # Extract resource type from ARN (e.g., arn:aws:s3:::bucket -> s3:bucket)
            resource_type = _get_resource_type_from_arn(arn)
            if resource_type not in resources:
                resources[resource_type] = []
            resources[resource_type].append(arn)

    return resources


def _get_resource_type_from_arn(arn: str) -> str:
    """Extract resource type from ARN."""
    # ARN format: arn:aws:service:region:account:resource-type/resource-id
    parts = arn.split(":")
    if len(parts) < 6:
        return "unknown"

    service = parts[2]
    resource_part = parts[5]

    # Handle different ARN formats
    if "/" in resource_part:
        resource_type = resource_part.split("/")[0]
    else:
        resource_type = resource_part

    return f"{service}:{resource_type}"


def destroy_stack(stack_name: str, region: str = "us-east-1") -> dict[str, Any]:
    """Delete all resources for a stack in correct dependency order.

    Order: Services -> Tasks -> Clusters -> APIs -> Lambdas -> Roles -> Buckets -> Log Groups

    Args:
        stack_name: Name of the stack to destroy.
        region: AWS region.

    Returns:
        Dictionary with deletion results.
    """
    from tests.shared.infrastructure_sdk.config import delete_outputs

    resources = find_resources_by_stack(stack_name, region)
    results: dict[str, Any] = {"deleted": [], "failed": [], "not_found": []}

    # Delete in dependency order
    for resource_type in RESOURCE_DELETION_ORDER:
        if resource_type not in resources:
            continue

        for arn in resources[resource_type]:
            try:
                _delete_resource(arn, resource_type, region)
                results["deleted"].append(arn)
            except ClientError as e:
                if e.response["Error"]["Code"] in [
                    "NoSuchEntity",
                    "ResourceNotFoundException",
                    "404",
                ]:
                    results["not_found"].append(arn)
                else:
                    results["failed"].append({"arn": arn, "error": str(e)})
            except Exception as e:
                results["failed"].append({"arn": arn, "error": str(e)})

    # Delete any remaining resources not in our ordered list
    for resource_type, arns in resources.items():
        if resource_type in RESOURCE_DELETION_ORDER:
            continue
        for arn in arns:
            if arn not in results["deleted"] and arn not in [
                r["arn"] for r in results["failed"] if isinstance(r, dict)
            ]:
                try:
                    _delete_resource(arn, resource_type, region)
                    results["deleted"].append(arn)
                except Exception as e:
                    results["failed"].append({"arn": arn, "error": str(e)})

    # Delete outputs file
    delete_outputs(stack_name)

    return results


def _delete_resource(arn: str, resource_type: str, region: str) -> None:
    """Delete a single resource by ARN."""
    if resource_type.startswith("ecs:service"):
        _delete_ecs_service(arn, region)
    elif resource_type.startswith("ecs:cluster"):
        _delete_ecs_cluster(arn, region)
    elif resource_type.startswith("apigateway"):
        _delete_api_gateway(arn, region)
    elif resource_type.startswith("lambda"):
        _delete_lambda(arn, region)
    elif resource_type.startswith("iam:role"):
        _delete_iam_role(arn, region)
    elif resource_type.startswith("s3"):
        _delete_s3_bucket(arn, region)
    elif resource_type.startswith("logs"):
        _delete_log_group(arn, region)
    elif resource_type.startswith("ecr"):
        _delete_ecr_repository(arn, region)
    elif resource_type.startswith("ec2:security-group"):
        _delete_security_group(arn, region)


def _delete_ecs_service(arn: str, region: str) -> None:
    """Delete ECS service."""
    client = get_boto3_client("ecs", region)
    # Extract cluster and service name from ARN
    # Format: arn:aws:ecs:region:account:service/cluster-name/service-name
    parts = arn.split("/")
    if len(parts) >= 3:
        cluster = parts[-2]
        service = parts[-1]
        # Scale to 0 first
        try:
            client.update_service(cluster=cluster, service=service, desiredCount=0)
            time.sleep(5)
        except ClientError:
            # Service may be draining or already at 0; proceed with deletion
            pass
        client.delete_service(cluster=cluster, service=service, force=True)


def _delete_ecs_cluster(arn: str, region: str) -> None:
    """Delete ECS cluster."""
    client = get_boto3_client("ecs", region)
    # Extract cluster name from ARN
    cluster_name = arn.split("/")[-1]
    client.delete_cluster(cluster=cluster_name)


def _delete_api_gateway(arn: str, region: str) -> None:
    """Delete API Gateway."""
    client = get_boto3_client("apigateway", region)
    # Extract API ID from ARN
    api_id = arn.split("/")[-1]
    client.delete_rest_api(restApiId=api_id)


def _delete_lambda(arn: str, region: str) -> None:
    """Delete Lambda function."""
    client = get_boto3_client("lambda", region)
    # Extract function name from ARN
    function_name = arn.split(":")[-1]
    client.delete_function(FunctionName=function_name)


def _delete_iam_role(arn: str, region: str) -> None:
    """Delete IAM role and all attached policies."""
    client = get_boto3_client("iam", region)
    role_name = arn.split("/")[-1]

    # Detach managed policies
    try:
        attached = client.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            client.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
    except ClientError:
        # Role may not exist or policies already detached
        pass

    # Delete inline policies
    try:
        inline = client.list_role_policies(RoleName=role_name)
        for policy_name in inline.get("PolicyNames", []):
            client.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
    except ClientError:
        # Role may not exist or inline policies already deleted
        pass

    # Delete instance profiles
    try:
        profiles = client.list_instance_profiles_for_role(RoleName=role_name)
        for profile in profiles.get("InstanceProfiles", []):
            client.remove_role_from_instance_profile(
                InstanceProfileName=profile["InstanceProfileName"],
                RoleName=role_name,
            )
    except ClientError:
        # Role may not exist or instance profiles already removed
        pass

    client.delete_role(RoleName=role_name)


def _delete_s3_bucket(arn: str, region: str) -> None:
    """Delete S3 bucket and all objects."""
    from tests.shared.infrastructure_sdk.resources.s3 import delete_bucket

    # Extract bucket name from ARN (arn:aws:s3:::bucket-name)
    bucket_name = arn.split(":")[-1]
    delete_bucket(bucket_name, region)


def _delete_log_group(arn: str, region: str) -> None:
    """Delete CloudWatch log group."""
    client = get_boto3_client("logs", region)
    # Extract log group name from ARN
    # Format: arn:aws:logs:region:account:log-group:name:*
    parts = arn.split(":")
    if len(parts) >= 7:
        log_group_name = parts[6]
        client.delete_log_group(logGroupName=log_group_name)


def _delete_ecr_repository(arn: str, region: str) -> None:
    """Delete ECR repository and all images."""
    client = get_boto3_client("ecr", region)
    # Extract repository name from ARN
    repo_name = arn.split("/")[-1]
    client.delete_repository(repositoryName=repo_name, force=True)


def _delete_security_group(arn: str, region: str) -> None:
    """Delete security group."""
    client = get_boto3_client("ec2", region)
    # Extract security group ID from ARN
    sg_id = arn.split("/")[-1]
    client.delete_security_group(GroupId=sg_id)
