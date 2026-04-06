"""ECR repository and image push."""

import subprocess
from pathlib import Path
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
)


def create_repository(name: str, stack_name: str, region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Create ECR repository.

    Args:
        name: Repository name.
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with repository info: uri, arn, name.
    """
    ecr_client = get_boto3_client("ecr", region)

    try:
        response = ecr_client.create_repository(
            repositoryName=name,
            imageScanningConfiguration={"scanOnPush": True},
            imageTagMutability="MUTABLE",
            tags=get_standard_tags(stack_name),
        )
        repo = response["repository"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryAlreadyExistsException":
            response = ecr_client.describe_repositories(repositoryNames=[name])
            repo = response["repositories"][0]
        else:
            raise

    return {
        "uri": repo["repositoryUri"],
        "arn": repo["repositoryArn"],
        "name": repo["repositoryName"],
    }


def get_login_password(region: str = DEFAULT_REGION) -> str:
    """Get ECR login password for docker.

    Args:
        region: AWS region.

    Returns:
        Authorization token for docker login.
    """
    ecr_client = get_boto3_client("ecr", region)

    response = ecr_client.get_authorization_token()
    auth_data = response["authorizationData"][0]

    # Token is base64 encoded "AWS:password"
    import base64

    token = base64.b64decode(auth_data["authorizationToken"]).decode()
    password = token.split(":")[1]

    return password


def get_registry_url(region: str = DEFAULT_REGION) -> str:
    """Get ECR registry URL.

    Args:
        region: AWS region.

    Returns:
        Registry URL (e.g., 123456789.dkr.ecr.us-east-1.amazonaws.com).
    """
    ecr_client = get_boto3_client("ecr", region)

    response = ecr_client.get_authorization_token()
    proxy_endpoint = response["authorizationData"][0]["proxyEndpoint"]

    # Remove https:// prefix
    return str(proxy_endpoint).replace("https://", "")


def docker_login(region: str = DEFAULT_REGION) -> None:
    """Perform docker login to ECR.

    Args:
        region: AWS region.
    """
    password = get_login_password(region)
    registry = get_registry_url(region)

    subprocess.run(
        ["docker", "login", "-u", "AWS", "--password-stdin", registry],
        input=password.encode(),
        check=True,
        capture_output=True,
    )


def build_and_push(
    dockerfile_path: Path,
    repository_uri: str,
    tag: str = "latest",
    platform: str = "linux/arm64",
    build_args: dict[str, str] | None = None,
    region: str = DEFAULT_REGION,
    context_dir: Path | None = None,
) -> str:
    """Build and push Docker image.

    Args:
        dockerfile_path: Path to Dockerfile or directory containing it.
        repository_uri: ECR repository URI.
        tag: Image tag.
        platform: Target platform (linux/arm64, linux/amd64).
        build_args: Build arguments.
        region: AWS region.
        context_dir: Optional build context directory (defaults to Dockerfile parent).

    Returns:
        Full image URI with tag.
    """
    # Ensure logged in
    docker_login(region)

    # Determine context directory and dockerfile path
    if dockerfile_path.is_file():
        dockerfile = str(dockerfile_path)
        if context_dir is None:
            context_dir = dockerfile_path.parent
    else:
        dockerfile = str(dockerfile_path / "Dockerfile")
        if context_dir is None:
            context_dir = dockerfile_path

    full_uri = f"{repository_uri}:{tag}"

    # Build command
    cmd = [
        "docker",
        "build",
        "--platform",
        platform,
        "-t",
        full_uri,
        "-f",
        dockerfile,
    ]

    if build_args:
        for key, value in build_args.items():
            cmd.extend(["--build-arg", f"{key}={value}"])

    cmd.append(str(context_dir))

    # Build
    subprocess.run(cmd, check=True, capture_output=True)

    # Push
    subprocess.run(["docker", "push", full_uri], check=True, capture_output=True)

    return full_uri


def delete_repository(name: str, region: str = DEFAULT_REGION) -> None:
    """Delete repository and all images.

    Args:
        name: Repository name.
        region: AWS region.
    """
    ecr_client = get_boto3_client("ecr", region)

    try:
        ecr_client.delete_repository(repositoryName=name, force=True)
    except ClientError as e:
        if e.response["Error"]["Code"] != "RepositoryNotFoundException":
            raise


def get_repository(name: str, region: str = DEFAULT_REGION) -> dict[str, Any] | None:
    """Get repository details.

    Args:
        name: Repository name.
        region: AWS region.

    Returns:
        Repository details or None if not found.
    """
    ecr_client = get_boto3_client("ecr", region)

    try:
        response = ecr_client.describe_repositories(repositoryNames=[name])
        if response["repositories"]:
            repo = response["repositories"][0]
            return {
                "uri": repo["repositoryUri"],
                "arn": repo["repositoryArn"],
                "name": repo["repositoryName"],
            }
        return None
    except ClientError as e:
        if e.response["Error"]["Code"] == "RepositoryNotFoundException":
            return None
        raise


def list_images(repository_name: str, region: str = DEFAULT_REGION) -> list[dict[str, Any]]:
    """List images in a repository.

    Args:
        repository_name: Repository name.
        region: AWS region.

    Returns:
        List of image details.
    """
    ecr_client = get_boto3_client("ecr", region)

    images = []
    paginator = ecr_client.get_paginator("describe_images")

    for page in paginator.paginate(repositoryName=repository_name):
        for image in page.get("imageDetails", []):
            images.append(
                {
                    "digest": image.get("imageDigest"),
                    "tags": image.get("imageTags", []),
                    "pushed_at": image.get("imagePushedAt"),
                    "size_bytes": image.get("imageSizeInBytes"),
                }
            )

    return images


def delete_images(
    repository_name: str,
    image_ids: list[dict[str, str]],
    region: str = DEFAULT_REGION,
) -> None:
    """Delete specific images from repository.

    Args:
        repository_name: Repository name.
        image_ids: List of image identifiers (imageDigest or imageTag).
        region: AWS region.
    """
    ecr_client = get_boto3_client("ecr", region)

    if not image_ids:
        return

    ecr_client.batch_delete_image(
        repositoryName=repository_name,
        imageIds=image_ids,
    )
