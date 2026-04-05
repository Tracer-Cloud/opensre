"""ECR repository management for EC2 deployment tests.

Handles creating a private ECR repository, building + pushing the Docker
image locally, and cleaning up afterwards.  This eliminates the slow
on-instance Docker build that was the main deployment bottleneck.
"""

from __future__ import annotations

import base64
import logging
import subprocess
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import DEFAULT_REGION, get_boto3_client

logger = logging.getLogger(__name__)

REPO_NAME = "tracer-ec2/opensre"
IMAGE_TAG = "latest"


def ensure_repository(region: str = DEFAULT_REGION) -> str:
    """Create the ECR repository if it doesn't exist. Return the repository URI."""
    ecr = get_boto3_client("ecr", region)
    try:
        resp = ecr.describe_repositories(repositoryNames=[REPO_NAME])
        uri = resp["repositories"][0]["repositoryUri"]
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
        resp = ecr.create_repository(
            repositoryName=REPO_NAME,
            imageScanningConfiguration={"scanOnPush": False},
            imageTagMutability="MUTABLE",
        )
        uri = resp["repository"]["repositoryUri"]
    logger.info("ECR repository: %s", uri)
    return uri


def get_ecr_login(region: str = DEFAULT_REGION) -> tuple[str, str, str]:
    """Return (username, password, registry_url) for docker login."""
    ecr = get_boto3_client("ecr", region)
    resp = ecr.get_authorization_token()
    auth = resp["authorizationData"][0]
    token = base64.b64decode(auth["authorizationToken"]).decode()
    username, password = token.split(":", 1)
    registry = auth["proxyEndpoint"]
    return username, password, registry


def build_and_push(repo_uri: str, repo_root: str, region: str = DEFAULT_REGION) -> str:
    """Build the Docker image locally and push to ECR. Return the full image URI."""
    image_uri = f"{repo_uri}:{IMAGE_TAG}"

    username, password, registry = get_ecr_login(region)
    subprocess.run(
        ["docker", "login", "--username", username, "--password-stdin", registry],
        input=password.encode(),
        check=True,
        capture_output=True,
    )

    subprocess.run(
        ["docker", "build", "-t", image_uri, "."],
        cwd=repo_root,
        check=True,
    )

    subprocess.run(
        ["docker", "push", image_uri],
        check=True,
    )

    return image_uri


def delete_repository(region: str = DEFAULT_REGION) -> None:
    """Delete the ECR repository and all images."""
    ecr = get_boto3_client("ecr", region)
    try:
        ecr.delete_repository(repositoryName=REPO_NAME, force=True)
        logger.info("Deleted ECR repository %s", REPO_NAME)
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise
        logger.info("ECR repository %s already deleted", REPO_NAME)


def get_repository_uri(region: str = DEFAULT_REGION) -> str | None:
    """Return the repository URI if it exists, else None."""
    ecr = get_boto3_client("ecr", region)
    try:
        resp = ecr.describe_repositories(repositoryNames=[REPO_NAME])
        return resp["repositories"][0]["repositoryUri"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            return None
        raise


def grant_ec2_pull(role_arn: str, region: str = DEFAULT_REGION) -> None:
    """Attach an inline policy to the EC2 role allowing ECR pull."""
    iam = get_boto3_client("iam", region)
    role_name = role_arn.rsplit("/", 1)[-1]

    ecr = get_boto3_client("ecr", region)
    resp = ecr.describe_repositories(repositoryNames=[REPO_NAME])
    repo_arn = resp["repositories"][0]["repositoryArn"]

    policy: dict[str, Any] = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Action": [
                    "ecr:GetDownloadUrlForLayer",
                    "ecr:BatchGetImage",
                    "ecr:BatchCheckLayerAvailability",
                ],
                "Resource": repo_arn,
            },
            {
                "Effect": "Allow",
                "Action": "ecr:GetAuthorizationToken",
                "Resource": "*",
            },
        ],
    }

    import json

    iam.put_role_policy(
        RoleName=role_name,
        PolicyName="ECRPullAccess",
        PolicyDocument=json.dumps(policy),
    )
