"""SSM Run Command and instance registration helpers for OpenSRE deployments."""

from __future__ import annotations

import logging
import time

from infrastructure.deployment.ec2.client import get_boto3_client
from infrastructure.deployment.ec2.config import (
    DEFAULT_REGION,
    SSM_CMD_POLL_ATTEMPTS,
    SSM_CMD_POLL_INTERVAL_SECONDS,
    SSM_REGISTRATION_MAX_ATTEMPTS,
    SSM_REGISTRATION_POLL_INTERVAL_SECONDS,
    SSM_SHELL_DOCUMENT,
    SSM_TERMINAL_STATUSES,
)

logger = logging.getLogger(__name__)


def wait_for_ssm_registration(
    instance_id: str,
    region: str = DEFAULT_REGION,
    poll_interval: int = SSM_REGISTRATION_POLL_INTERVAL_SECONDS,
    max_attempts: int = SSM_REGISTRATION_MAX_ATTEMPTS,
) -> bool:
    """Wait until the SSM agent on the instance registers and becomes online."""
    ssm = get_boto3_client("ssm", region)

    for attempt in range(max_attempts):
        try:
            resp = ssm.describe_instance_information(
                Filters=[{"Key": "InstanceIds", "Values": [instance_id]}]
            )
            instances = resp.get("InstanceInformationList", [])
            if instances and instances[0].get("PingStatus") == "Online":
                logger.info("SSM agent online for %s after %d attempts", instance_id, attempt + 1)
                return True
        except Exception as exc:  # noqa: BLE001
            logger.debug("SSM describe attempt %d: %s", attempt + 1, exc)

        if attempt < max_attempts - 1:
            time.sleep(poll_interval)

    raise TimeoutError(
        f"SSM agent on {instance_id} did not come online after {max_attempts * poll_interval}s"
    )


def run_ssm_shell_command(
    instance_id: str,
    commands: list[str],
    region: str = DEFAULT_REGION,
    poll_interval: int = SSM_CMD_POLL_INTERVAL_SECONDS,
    max_poll_attempts: int = SSM_CMD_POLL_ATTEMPTS,
) -> dict[str, str]:
    """Execute shell commands on an EC2 instance via SSM Run Command."""
    ssm = get_boto3_client("ssm", region)

    resp = ssm.send_command(
        InstanceIds=[instance_id],
        DocumentName=SSM_SHELL_DOCUMENT,
        Parameters={"commands": commands},
    )
    command_id = resp["Command"]["CommandId"]
    logger.debug("SSM command %s sent to %s", command_id, instance_id)

    for attempt in range(max_poll_attempts):
        time.sleep(poll_interval)
        try:
            inv = ssm.get_command_invocation(
                CommandId=command_id,
                InstanceId=instance_id,
            )
        except ssm.exceptions.InvocationDoesNotExist:
            logger.debug("SSM invocation %s not yet available, retrying...", command_id)
            continue

        status = inv["Status"]
        if status in SSM_TERMINAL_STATUSES:
            result = {
                "status": status,
                "stdout": inv.get("StandardOutputContent", ""),
                "stderr": inv.get("StandardErrorContent", ""),
            }
            logger.debug(
                "SSM command %s finished: status=%s stdout=%r",
                command_id,
                status,
                result["stdout"][:200],
            )
            return result

        logger.debug(
            "SSM command %s status=%s attempt=%d/%d",
            command_id,
            status,
            attempt + 1,
            max_poll_attempts,
        )

    raise TimeoutError(
        f"SSM command {command_id} on {instance_id} did not complete "
        f"within {max_poll_attempts * poll_interval}s"
    )
