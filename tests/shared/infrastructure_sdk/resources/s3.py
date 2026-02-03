"""S3 bucket creation and management."""

import json
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_boto3_resource,
    get_standard_tags,
)


def create_bucket(name: str, stack_name: str, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Create S3 bucket with tags.

    Args:
        name: Bucket name (must be globally unique).
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with bucket info: name, arn, region.
    """
    s3_client = get_boto3_client("s3", region)

    # Create bucket (LocationConstraint needed for non-us-east-1)
    create_config: dict[str, Any] = {}
    if region != "us-east-1":
        create_config["CreateBucketConfiguration"] = {"LocationConstraint": region}

    try:
        s3_client.create_bucket(Bucket=name, **create_config)
    except ClientError as e:
        if e.response["Error"]["Code"] == "BucketAlreadyOwnedByYou":
            pass  # Bucket already exists, continue
        else:
            raise

    # Add tags
    s3_client.put_bucket_tagging(
        Bucket=name,
        Tagging={"TagSet": get_standard_tags(stack_name)},
    )

    # Block public access by default
    s3_client.put_public_access_block(
        Bucket=name,
        PublicAccessBlockConfiguration={
            "BlockPublicAcls": True,
            "IgnorePublicAcls": True,
            "BlockPublicPolicy": True,
            "RestrictPublicBuckets": True,
        },
    )

    return {
        "name": name,
        "arn": f"arn:aws:s3:::{name}",
        "region": region,
    }


def delete_bucket(name: str, region: str = DEFAULT_REGION) -> None:
    """Delete bucket and all objects.

    Args:
        name: Bucket name.
        region: AWS region.
    """
    s3_resource = get_boto3_resource("s3", region)
    bucket = s3_resource.Bucket(name)

    import contextlib

    with contextlib.suppress(ClientError):
        # Delete all objects (including versions)
        bucket.object_versions.delete()

    with contextlib.suppress(ClientError):
        # Delete all objects (non-versioned)
        bucket.objects.all().delete()

    try:
        bucket.delete()
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchBucket":
            raise


def grant_read(bucket_name: str, role_arn: str, region: str = DEFAULT_REGION) -> None:
    """Add bucket policy for read access.

    Args:
        bucket_name: Bucket name.
        role_arn: ARN of the IAM role to grant access.
        region: AWS region.
    """
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowRead",
                "Effect": "Allow",
                "Principal": {"AWS": role_arn},
                "Action": ["s3:GetObject", "s3:ListBucket"],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            }
        ],
    }
    _merge_bucket_policy(bucket_name, policy, region)


def grant_write(bucket_name: str, role_arn: str, region: str = DEFAULT_REGION) -> None:
    """Add bucket policy for write access.

    Args:
        bucket_name: Bucket name.
        role_arn: ARN of the IAM role to grant access.
        region: AWS region.
    """
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowWrite",
                "Effect": "Allow",
                "Principal": {"AWS": role_arn},
                "Action": ["s3:PutObject", "s3:DeleteObject"],
                "Resource": [f"arn:aws:s3:::{bucket_name}/*"],
            }
        ],
    }
    _merge_bucket_policy(bucket_name, policy, region)


def grant_read_write(bucket_name: str, role_arn: str, region: str = DEFAULT_REGION) -> None:
    """Add bucket policy for full read/write access.

    Args:
        bucket_name: Bucket name.
        role_arn: ARN of the IAM role to grant access.
        region: AWS region.
    """
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowReadWrite",
                "Effect": "Allow",
                "Principal": {"AWS": role_arn},
                "Action": [
                    "s3:GetObject",
                    "s3:PutObject",
                    "s3:DeleteObject",
                    "s3:ListBucket",
                ],
                "Resource": [
                    f"arn:aws:s3:::{bucket_name}",
                    f"arn:aws:s3:::{bucket_name}/*",
                ],
            }
        ],
    }
    _merge_bucket_policy(bucket_name, policy, region)


def _merge_bucket_policy(bucket_name: str, new_policy: dict[str, Any], region: str) -> None:
    """Merge a new policy with existing bucket policy."""
    s3_client = get_boto3_client("s3", region)

    # Get existing policy
    try:
        existing = s3_client.get_bucket_policy(Bucket=bucket_name)
        existing_policy = json.loads(existing["Policy"])
    except ClientError as e:
        if e.response["Error"]["Code"] == "NoSuchBucketPolicy":
            existing_policy = {"Version": "2012-10-17", "Statement": []}
        else:
            raise

    # Merge statements
    existing_sids = {s.get("Sid") for s in existing_policy.get("Statement", [])}
    for statement in new_policy.get("Statement", []):
        if statement.get("Sid") not in existing_sids:
            existing_policy["Statement"].append(statement)

    s3_client.put_bucket_policy(
        Bucket=bucket_name,
        Policy=json.dumps(existing_policy),
    )


def bucket_exists(name: str, region: str = DEFAULT_REGION) -> bool:
    """Check if bucket exists.

    Args:
        name: Bucket name.
        region: AWS region.

    Returns:
        True if bucket exists.
    """
    s3_client = get_boto3_client("s3", region)
    try:
        s3_client.head_bucket(Bucket=name)
        return True
    except ClientError:
        return False
