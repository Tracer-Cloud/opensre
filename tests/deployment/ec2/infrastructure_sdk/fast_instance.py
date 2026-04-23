"""Fast EC2 instance launcher for hello-world smoke test.

Optimised for speed (<60 s to first HTTP response) by using an
ECS-optimized AMI (Docker pre-installed) and a tiny container image.
"""

from __future__ import annotations

import logging
import time

import requests

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
)

logger = logging.getLogger(__name__)

INSTANCE_TYPE = "t3.micro"
HELLO_PORT = 8080
HELLO_IMAGE_TAG = "hello-world"
HEALTH_POLL_INTERVAL = 2
HEALTH_MAX_ATTEMPTS = 30  # 60 s total

ECR_REGION = "us-east-1"
ECR_ACCOUNT_ID = "395261708130"
ECR_REPO = "opensre"
HELLO_IMAGE_URI = (
    f"{ECR_ACCOUNT_ID}.dkr.ecr.{ECR_REGION}.amazonaws.com/{ECR_REPO}:{HELLO_IMAGE_TAG}"
)


def get_ecs_optimized_ami(region: str = DEFAULT_REGION) -> str:
    """Look up the latest ECS-optimized Amazon Linux 2023 x86_64 AMI.

    This AMI ships with Docker and the ECS agent pre-installed, eliminating
    the need to ``dnf install docker`` in user data.
    """
    ssm = get_boto3_client("ssm", region)
    resp = ssm.get_parameter(
        Name="/aws/service/ecs/optimized-ami/amazon-linux-2023/recommended/image_id"
    )
    return str(resp["Parameter"]["Value"])


def generate_hello_user_data() -> str:
    """Minimal user-data script: start Docker, pull, and run.

    No package installs, no IAM propagation wait — the ECS-optimized AMI
    already has Docker and the AWS CLI.
    """
    return f"""\
#!/bin/bash
exec > /var/log/hello-deploy.log 2>&1
set -euo pipefail

echo "=== Starting Docker ==="
systemctl start docker

echo "=== Authenticating with ECR ==="
aws ecr get-login-password --region {ECR_REGION} | \
  docker login --username AWS --password-stdin \
  {ECR_ACCOUNT_ID}.dkr.ecr.{ECR_REGION}.amazonaws.com

echo "=== Pulling hello-world image ==="
docker pull {HELLO_IMAGE_URI}

echo "=== Starting container ==="
docker run -d --name hello -p {HELLO_PORT}:{HELLO_PORT} {HELLO_IMAGE_URI}

echo "=== Done ==="
"""


def wait_for_hello(
    public_ip: str,
    port: int = HELLO_PORT,
    max_attempts: int = HEALTH_MAX_ATTEMPTS,
) -> bool:
    """Poll ``GET /ping`` until the hello-world server responds.

    Raises:
        TimeoutError: If the server doesn't respond in time.
    """
    url = f"http://{public_ip}:{port}/ping"

    for attempt in range(max_attempts):
        try:
            resp = requests.get(url, timeout=3)
            if resp.status_code == 200:
                logger.info("Hello-world responded after %d attempts", attempt + 1)
                return True
            logger.debug("Ping returned %d", resp.status_code)
        except requests.exceptions.RequestException as exc:
            logger.debug("Ping attempt %d: %s", attempt + 1, exc)

        if attempt < max_attempts - 1:
            time.sleep(HEALTH_POLL_INTERVAL)

    raise TimeoutError(
        f"Hello-world at {public_ip}:{port} not reachable "
        f"after {max_attempts * HEALTH_POLL_INTERVAL}s"
    )
