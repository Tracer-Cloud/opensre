"""IAM role creation with policies."""

import json
import time
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
)

# Common trust policies
LAMBDA_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "lambda.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

ECS_TASK_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "ecs-tasks.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

# Common managed policy ARNs
LAMBDA_BASIC_EXECUTION_POLICY = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
ECS_TASK_EXECUTION_POLICY = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"


def create_lambda_execution_role(
    name: str, stack_name: str, region: str = DEFAULT_REGION
) -> dict[str, Any]:
    """Create basic Lambda execution role with CloudWatch logs policy.

    Args:
        name: Role name.
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with role info: arn, name.
    """
    iam_client = get_boto3_client("iam", region)

    role = _create_role(
        client=iam_client,
        name=name,
        trust_policy=LAMBDA_TRUST_POLICY,
        stack_name=stack_name,
        description="Lambda execution role",
    )

    # Attach basic execution policy
    attach_policy(name, LAMBDA_BASIC_EXECUTION_POLICY, region)

    return role


def create_ecs_task_role(
    name: str, stack_name: str, region: str = DEFAULT_REGION
) -> dict[str, Any]:
    """Create ECS task role.

    Args:
        name: Role name.
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with role info: arn, name.
    """
    iam_client = get_boto3_client("iam", region)

    return _create_role(
        client=iam_client,
        name=name,
        trust_policy=ECS_TASK_TRUST_POLICY,
        stack_name=stack_name,
        description="ECS task role",
    )


def create_ecs_execution_role(
    name: str, stack_name: str, region: str = DEFAULT_REGION
) -> dict[str, Any]:
    """Create ECS execution role with ECR and logs permissions.

    Args:
        name: Role name.
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with role info: arn, name.
    """
    iam_client = get_boto3_client("iam", region)

    role = _create_role(
        client=iam_client,
        name=name,
        trust_policy=ECS_TASK_TRUST_POLICY,
        stack_name=stack_name,
        description="ECS execution role",
    )

    # Attach execution policy (includes ECR and logs)
    attach_policy(name, ECS_TASK_EXECUTION_POLICY, region)

    return role


def _create_role(
    client: Any,
    name: str,
    trust_policy: dict[str, Any],
    stack_name: str,
    description: str,
) -> dict[str, Any]:
    """Create an IAM role."""
    tags = get_standard_tags(stack_name)

    try:
        response = client.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps(trust_policy),
            Description=description,
            Tags=tags,
        )
        role_arn = response["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            # Role exists, get its ARN
            response = client.get_role(RoleName=name)
            role_arn = response["Role"]["Arn"]
        else:
            raise

    # Wait for role to be usable (IAM is eventually consistent)
    time.sleep(5)

    return {
        "arn": role_arn,
        "name": name,
    }


def attach_policy(role_name: str, policy_arn: str, region: str = DEFAULT_REGION) -> None:
    """Attach managed policy to role.

    Args:
        role_name: Role name.
        policy_arn: ARN of managed policy.
        region: AWS region.
    """
    iam_client = get_boto3_client("iam", region)

    try:
        iam_client.attach_role_policy(
            RoleName=role_name,
            PolicyArn=policy_arn,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise


def detach_policy(role_name: str, policy_arn: str, region: str = DEFAULT_REGION) -> None:
    """Detach managed policy from role.

    Args:
        role_name: Role name.
        policy_arn: ARN of managed policy.
        region: AWS region.
    """
    iam_client = get_boto3_client("iam", region)

    try:
        iam_client.detach_role_policy(
            RoleName=role_name,
            PolicyArn=policy_arn,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise


def put_role_policy(
    role_name: str,
    policy_name: str,
    policy_document: dict[str, Any],
    region: str = DEFAULT_REGION,
) -> None:
    """Add inline policy to role.

    Args:
        role_name: Role name.
        policy_name: Name for the inline policy.
        policy_document: Policy document as dict.
        region: AWS region.
    """
    iam_client = get_boto3_client("iam", region)

    iam_client.put_role_policy(
        RoleName=role_name,
        PolicyName=policy_name,
        PolicyDocument=json.dumps(policy_document),
    )


def delete_role_policy(role_name: str, policy_name: str, region: str = DEFAULT_REGION) -> None:
    """Delete inline policy from role.

    Args:
        role_name: Role name.
        policy_name: Policy name.
        region: AWS region.
    """
    iam_client = get_boto3_client("iam", region)

    try:
        iam_client.delete_role_policy(
            RoleName=role_name,
            PolicyName=policy_name,
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise


def delete_role(name: str, region: str = DEFAULT_REGION) -> None:
    """Delete role and all attached policies.

    Args:
        name: Role name.
        region: AWS region.
    """
    iam_client = get_boto3_client("iam", region)

    # Detach managed policies
    try:
        attached = iam_client.list_attached_role_policies(RoleName=name)
        for policy in attached.get("AttachedPolicies", []):
            iam_client.detach_role_policy(RoleName=name, PolicyArn=policy["PolicyArn"])
    except ClientError:
        # Role may not exist or policies already detached
        pass

    # Delete inline policies
    try:
        inline = iam_client.list_role_policies(RoleName=name)
        for policy_name in inline.get("PolicyNames", []):
            iam_client.delete_role_policy(RoleName=name, PolicyName=policy_name)
    except ClientError:
        # Role may not exist or inline policies already deleted
        pass

    # Delete role
    try:
        iam_client.delete_role(RoleName=name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise


def get_role(name: str, region: str = DEFAULT_REGION) -> dict[str, Any] | None:
    """Get role details.

    Args:
        name: Role name.
        region: AWS region.

    Returns:
        Role details or None if not found.
    """
    iam_client = get_boto3_client("iam", region)

    try:
        response = iam_client.get_role(RoleName=name)
        return {
            "arn": response["Role"]["Arn"],
            "name": response["Role"]["RoleName"],
        }
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchEntity":
            return None
        raise


def get_account_id(region: str = DEFAULT_REGION) -> str:
    """Get current AWS account ID.

    Args:
        region: AWS region.

    Returns:
        Account ID string.
    """
    sts_client = get_boto3_client("sts", region)
    return str(sts_client.get_caller_identity()["Account"])
