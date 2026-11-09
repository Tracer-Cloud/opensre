"""AWS deployment configuration constants."""

from __future__ import annotations

# ─── Region ───────────────────────────────────────────────────────────────────
DEFAULT_REGION = "us-east-1"

# ─── Boto3 client ─────────────────────────────────────────────────────────────
BOTO3_RETRY_MAX_ATTEMPTS = 3
BOTO3_CONNECT_TIMEOUT_SECONDS = 10
BOTO3_READ_TIMEOUT_SECONDS = 30

# ─── Resource tags ────────────────────────────────────────────────────────────
STACK_TAG_KEY = "tracer:stack"
MANAGED_TAG_KEY = "tracer:managed"
MANAGED_TAG_VALUE = "sdk"

# ─── EC2 instance ─────────────────────────────────────────────────────────────
INSTANCE_TYPE = "t3.micro"
WEB_API_PORT = 8000
WEB_API_INGRESS_CIDR_ENV = "OPENSRE_WEB_API_INGRESS_CIDR"
WEB_API_INGRESS_CIDR_DEFAULT = "0.0.0.0/0"
AL2023_AMI_SSM_PARAMETER = "/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64"
# Ubuntu 22.04 LTS (Jammy) — ships glibc 2.35, required by the pre-built opensre
# PyInstaller binary.  AL2023 only ships glibc 2.34 so the binary fails there.
UBUNTU2204_AMI_SSM_PARAMETER = (
    "/aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id"
)
EC2_ROOT_DEVICE_NAME = "/dev/xvda"  # Amazon Linux 2023
EC2_UBUNTU_ROOT_DEVICE_NAME = "/dev/sda1"  # Ubuntu official AMIs (22.04+)
EC2_VOLUME_SIZE_GB = 30
EC2_VOLUME_TYPE = "gp3"
EC2_INSTANCE_ROLE_DESCRIPTION = "EC2 instance role for OpenSRE deployment"
EC2_WAITER_DELAY_SECONDS = 10
EC2_WAITER_MAX_ATTEMPTS = 30

# ─── IAM managed policy ARNs ──────────────────────────────────────────────────
ECR_READ_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
BEDROCK_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonBedrockFullAccess"
SSM_MANAGED_POLICY_ARN = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"

# ─── IAM propagation ──────────────────────────────────────────────────────────
IAM_PROFILE_PROPAGATION_SECONDS = 10

# ─── SSM ──────────────────────────────────────────────────────────────────────
SSM_REGISTRATION_POLL_INTERVAL_SECONDS = 10
SSM_REGISTRATION_MAX_ATTEMPTS = 30
SSM_CMD_POLL_INTERVAL_SECONDS = 5
SSM_CMD_POLL_ATTEMPTS = 24
SSM_PROVISION_CMD_POLL_INTERVAL_SECONDS = 10
SSM_PROVISION_CMD_POLL_ATTEMPTS = 60
SSM_SHELL_DOCUMENT = "AWS-RunShellScript"
SSM_TERMINAL_STATUSES = ("Success", "Failed", "Cancelled", "TimedOut", "Undeliverable")

# ─── Gateway health checks (via SSM) ──────────────────────────────────────────
GATEWAY_HEALTH_POLL_INTERVAL_SECONDS = 15
GATEWAY_HEALTH_MAX_ATTEMPTS = 60
GATEWAY_LOG_TAIL_LINES = 200
# Transport-agnostic ready line from GatewayController after components start.
# Also accept legacy per-transport lines so older images still pass health waits.
GATEWAY_READY_LOG_SENTINEL = "[gateway] ready"
GATEWAY_READY_LOG_SENTINELS: tuple[str, ...] = (
    GATEWAY_READY_LOG_SENTINEL,
    "polling started",  # Telegram long poll (pre-unified images)
    "socket mode connected",  # Slack Socket Mode (pre-unified images)
)


def logs_contain_gateway_ready(stdout: str) -> bool:
    """True when gateway logs show any accepted ready sentinel."""
    return any(sentinel in stdout for sentinel in GATEWAY_READY_LOG_SENTINELS)


# ─── Gateway AMI baking ────────────────────────────────────────────────────────
GATEWAY_AMI_NAME_PREFIX = "opensre-gateway"
GATEWAY_BUILDER_INSTANCE_TYPE = "t3.small"
GATEWAY_AMI_WAITER_DELAY_SECONDS = 30
GATEWAY_AMI_WAITER_MAX_ATTEMPTS = 40  # 20 minutes max
GATEWAY_AMI_GIT_REF_ENV = "OPENSRE_GATEWAY_GIT_REF"
GATEWAY_AMI_ID_ENV = "OPENSRE_GATEWAY_AMI_ID"
GATEWAY_AMI_DESTROY_PURGE_ENV = "OPENSRE_GATEWAY_DESTROY_PURGE_AMI"
