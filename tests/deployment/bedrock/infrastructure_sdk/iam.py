"""IAM role for Bedrock Agent with bedrock.amazonaws.com trust policy."""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
)

BEDROCK_TRUST_POLICY = {
    "Version": "2012-10-17",
    "Statement": [
        {
            "Effect": "Allow",
            "Principal": {"Service": "bedrock.amazonaws.com"},
            "Action": "sts:AssumeRole",
        }
    ],
}

BEDROCK_FULL_ACCESS_POLICY = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"


def create_bedrock_agent_role(
    name: str,
    stack_name: str,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create an IAM role that Bedrock Agent can assume.

    Args:
        name: Role name.
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with role info: arn, name.
    """
    iam_client = get_boto3_client("iam", region)
    tags = get_standard_tags(stack_name)

    try:
        response = iam_client.create_role(
            RoleName=name,
            AssumeRolePolicyDocument=json.dumps(BEDROCK_TRUST_POLICY),
            Description="Bedrock Agent execution role for OpenSRE deployment tests",
            Tags=tags,
        )
        role_arn = response["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            response = iam_client.get_role(RoleName=name)
            role_arn = response["Role"]["Arn"]
        else:
            raise

    try:
        iam_client.attach_role_policy(RoleName=name, PolicyArn=BEDROCK_FULL_ACCESS_POLICY)
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise

    # IAM is eventually consistent; wait for the role to be usable
    time.sleep(10)

    return {"arn": role_arn, "name": name}


def delete_bedrock_agent_role(name: str, region: str = DEFAULT_REGION) -> None:
    """Delete Bedrock Agent IAM role and all attached policies.

    Args:
        name: Role name.
        region: AWS region.
    """
    iam_client = get_boto3_client("iam", region)

    try:
        attached = iam_client.list_attached_role_policies(RoleName=name)
        for policy in attached.get("AttachedPolicies", []):
            iam_client.detach_role_policy(RoleName=name, PolicyArn=policy["PolicyArn"])
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to detach policies from role %s: %s", name, e)

    try:
        inline = iam_client.list_role_policies(RoleName=name)
        for policy_name in inline.get("PolicyNames", []):
            iam_client.delete_role_policy(RoleName=name, PolicyName=policy_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to delete inline policies from role %s: %s", name, e)

    try:
        iam_client.delete_role(RoleName=name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise
