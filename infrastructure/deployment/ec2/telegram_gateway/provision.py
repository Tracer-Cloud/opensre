"""SSM provisioning and readiness checks for the gateway systemd service."""

from __future__ import annotations

import base64
import logging
import shlex
import time

from infrastructure.deployment.ec2.config import (
    DEFAULT_REGION,
    GATEWAY_HEALTH_MAX_ATTEMPTS,
    GATEWAY_HEALTH_POLL_INTERVAL_SECONDS,
    GATEWAY_LOG_TAIL_LINES,
    SSM_PROVISION_CMD_POLL_ATTEMPTS,
    SSM_PROVISION_CMD_POLL_INTERVAL_SECONDS,
    logs_contain_gateway_ready,
)
from infrastructure.deployment.ec2.ssm import run_ssm_shell_command

logger = logging.getLogger(__name__)

_ENV_DIR = "/etc/opensre"
_GATEWAY_ENV_PATH = f"{_ENV_DIR}/gateway.env"
_SERVICE_NAME = "opensre-gateway"


def _env_file_content(env_vars: dict[str, str]) -> str:
    """Return systemd EnvironmentFile content for the given variables."""
    lines: list[str] = []
    for key in sorted(env_vars):
        value = env_vars[key]
        if "\n" in value or "\r" in value:
            raise ValueError(f"Environment variable {key} must not contain newlines")
        lines.append(f"{key}={value}")
    return "\n".join(lines) + "\n"


def _write_env_file_commands(path: str, content: str) -> list[str]:
    """Return shell commands that write ``content`` to ``path`` via base64."""
    encoded = base64.b64encode(content.encode()).decode("ascii")
    quoted_path = shlex.quote(path)
    return [
        f"mkdir -p {shlex.quote(_ENV_DIR)}",
        f"echo {shlex.quote(encoded)} | base64 -d > {quoted_path}",
        f"chmod 640 {quoted_path}",
        f"chown root:opensre {quoted_path}",
    ]


def provision_gateway_via_ssm(
    instance_id: str,
    *,
    env_vars: dict[str, str] | None = None,
    region: str = DEFAULT_REGION,
) -> None:
    """Write the gateway env file and restart the systemd service via SSM.

    The env file is transferred as base64 so values with special shell
    characters (quotes, spaces, etc.) are never interpreted by the shell.
    """
    gateway_env = {"MODE": "gateway", **(env_vars or {})}
    env_content = _env_file_content(gateway_env)

    # SSM AWS-RunShellScript runs via /bin/sh (dash on Ubuntu).  pipefail is
    # bash-only; use POSIX -eu so provisioning works on both AL2023 and Ubuntu.
    commands = [
        "set -eu",
        *_write_env_file_commands(_GATEWAY_ENV_PATH, env_content),
        f"systemctl restart {shlex.quote(_SERVICE_NAME)}",
    ]

    result = run_ssm_shell_command(
        instance_id=instance_id,
        commands=commands,
        region=region,
        poll_interval=SSM_PROVISION_CMD_POLL_INTERVAL_SECONDS,
        max_poll_attempts=SSM_PROVISION_CMD_POLL_ATTEMPTS,
    )
    status = result.get("status", "")
    if status != "Success":
        stderr = result.get("stderr", "").strip()
        raise RuntimeError(
            f"Failed to provision gateway on {instance_id}: "
            f"status={status}, stderr={stderr or 'none'}"
        )


def wait_for_gateway_ready(
    instance_id: str,
    *,
    region: str = DEFAULT_REGION,
    poll_interval: int = GATEWAY_HEALTH_POLL_INTERVAL_SECONDS,
    max_attempts: int = GATEWAY_HEALTH_MAX_ATTEMPTS,
) -> None:
    """Poll until the opensre-gateway systemd service is active and has logged the ready sentinel.

    Raises:
        TimeoutError: When the service does not reach a ready state in time.
    """
    service = shlex.quote(_SERVICE_NAME)
    for attempt in range(max_attempts):
        try:
            result = run_ssm_shell_command(
                instance_id=instance_id,
                commands=[
                    f"systemctl is-active {service} || true",
                    f"journalctl -u {service} -n {GATEWAY_LOG_TAIL_LINES} --no-pager 2>/dev/null || true",
                ],
                region=region,
            )
            stdout = result.get("stdout", "")
            lines = [ln.strip() for ln in stdout.strip().splitlines() if ln.strip()]
            service_active = bool(lines) and lines[0] == "active"
            sentinel_found = logs_contain_gateway_ready(stdout)

            if service_active and sentinel_found:
                logger.info(
                    "Gateway service ready on %s after %d attempts",
                    instance_id,
                    attempt + 1,
                )
                return

            logger.debug(
                "Gateway not ready yet (attempt %d/%d): active=%s sentinel=%s",
                attempt + 1,
                max_attempts,
                service_active,
                sentinel_found,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("SSM gateway check attempt %d failed: %s", attempt + 1, exc)

        if attempt < max_attempts - 1:
            time.sleep(poll_interval)

    raise TimeoutError(
        f"Gateway service on {instance_id} did not become ready "
        f"after {max_attempts * poll_interval}s"
    )
