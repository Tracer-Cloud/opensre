"""Start a new server and install the gateway on it, with no image built first.

The other path in this package builds a server image once
(:mod:`infrastructure.deployment.ec2.telegram_gateway.build_server_image`) so later servers start
ready. This one skips that: it launches a plain server and installs the gateway
there and then, by downloading the published installer. Slower per server, but
nothing to rebuild when the code changes.
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from botocore.exceptions import ClientError

from config.constants import OPENSRE_HOME_DIR
from infrastructure.deployment.ec2.config import (
    DEFAULT_REGION,
    EC2_UBUNTU_ROOT_DEVICE_NAME,
    INSTANCE_TYPE,
    SSM_MANAGED_POLICY_ARN,
)
from infrastructure.deployment.ec2.ec2 import (
    create_instance_profile,
    create_stack_security_group,
    delete_instance_profile,
    delete_stack_security_group,
    find_stack_instance_ids,
    get_latest_ubuntu2204_ami,
    launch_instance,
    terminate_instance,
    wait_for_running,
)
from infrastructure.deployment.ec2.ssm import run_ssm_shell_command, wait_for_ssm_registration
from infrastructure.deployment.ec2.telegram_gateway.provision import (
    provision_gateway_via_ssm,
    wait_for_gateway_ready,
)

# ── Constants ──────────────────────────────────────────────────────────────────

# Names live AWS resources (IAM profile, role, security group) for servers this
# module created. Renaming it would orphan anything already deployed, so it stays
# as-is even though the module no longer says "direct".
_DIRECT_STACK_NAME = "opensre-gateway-direct"
_STACK_SUFFIX_ENV = "OPENSRE_STACK_SUFFIX"
_OUTPUTS_DIR = OPENSRE_HOME_DIR / "deployments"

_INSTALL_URL_HOST = "install.opensre.com"


def _validated_install_url(url: str) -> str:
    """Return ``url`` when it targets the published installer host exactly."""
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname != _INSTALL_URL_HOST:
        raise ValueError(f"Install URL must be https://{_INSTALL_URL_HOST}/, got {url!r}")
    if parsed.username or parsed.password:
        raise ValueError("Install URL must not include credentials")
    return url


# The published install script URL (hostname checked at import time).
_INSTALL_URL = _validated_install_url(f"https://{_INSTALL_URL_HOST}")

# Where the binary is installed on the instance.  /usr/local/bin is always on
# the system PATH, accessible to the opensre system user running under systemd.
_BINARY_INSTALL_DIR = "/usr/local/bin"
_SERVICE_NAME = "opensre-gateway"

# SSM poll budget for the install step.  The curl download is ~40-80 MB so
# 3-5 minutes is typical; 30 attempts × 10 s = 5 min ceiling.
_INSTALL_MAX_POLL_ATTEMPTS = 30

# Systemd unit content for the curl-install binary path.
# ExecStart points to /usr/local/bin/opensre (not the venv path the image build uses).
_SERVICE_UNIT = """\
[Unit]
Description=OpenSRE Gateway Daemon
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
User=opensre
Group=opensre

# HOME must be set explicitly so ~/.opensre/* paths resolve correctly
# for the gateway SQLite db, session files, integrations config, etc.
Environment=HOME=/var/lib/opensre-gateway

# Env file written by SSM provisioning (deploy step)
EnvironmentFile=/etc/opensre/gateway.env

ExecStart=/usr/local/bin/opensre gateway start --foreground

Restart=always
RestartSec=5
TimeoutStartSec=30
TimeoutStopSec=20

# Keep logs in journald; rotate handled by system journal limits
StandardOutput=journal
StandardError=journal
SyslogIdentifier=opensre-gateway

[Install]
WantedBy=multi-user.target
"""

# ── Stack helpers ──────────────────────────────────────────────────────────────


def _direct_stack_name() -> str:
    suffix = os.getenv(_STACK_SUFFIX_ENV, "").strip()
    return f"{_DIRECT_STACK_NAME}-{suffix}" if suffix else _DIRECT_STACK_NAME


def _outputs_path() -> Path:
    return _OUTPUTS_DIR / f"{_direct_stack_name()}.json"


def _save_outputs(outputs: dict[str, Any]) -> None:
    path = _outputs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(outputs, indent=2, default=str) + "\n", encoding="utf-8")


def _load_outputs() -> dict[str, Any]:
    path = _outputs_path()
    if not path.exists():
        raise FileNotFoundError(
            f"No direct-deploy outputs found for stack '{_direct_stack_name()}'. "
            "Run `make install-gateway-on-new-server` first."
        )
    result = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(result, dict):
        raise ValueError("Direct-deploy outputs file is malformed.")
    return result


def _outputs_exist() -> bool:
    return _outputs_path().exists()


def _delete_outputs() -> None:
    path = _outputs_path()
    if path.exists():
        path.unlink()


# ── Install commands ───────────────────────────────────────────────────────────


def _build_curl_install_commands() -> list[str]:
    """Return SSM shell commands that install the gateway via the published binary.

    Uses ``curl -fsSL https://install.opensre.com | bash`` instead of
    git-clone + pip-install.  The pre-built PyInstaller binary is ~40-80 MB and
    requires no Python, pip, git, or venv on the instance — so there is no OOM
    risk on a t3.micro.

    The binary is installed to ``/usr/local/bin/opensre`` so it is accessible
    to the ``opensre`` system user that runs the gateway under systemd.
    """
    encoded_service = base64.b64encode(_SERVICE_UNIT.encode()).decode("ascii")
    install_dir = shlex.quote(_BINARY_INSTALL_DIR)
    install_url = shlex.quote(_INSTALL_URL)

    return [
        # SSM AWS-RunShellScript executes via /bin/sh (dash on Ubuntu), which
        # does not support pipefail.  Use -eu only; pipefail is bash-specific.
        "set -eu",
        # SSM runs as root but does not always set HOME.  The install script
        # references $HOME even when OPENSRE_INSTALL_DIR is overridden, so we
        # must declare it explicitly to avoid an "unbound variable" abort under
        # `set -u`.
        "export HOME=/root",
        # Create system user and persistent data dirs.
        # || true makes useradd idempotent (exit 9 = user already exists) and
        # avoids any bash-vs-dash portability issue with the `id` guard pattern.
        # --no-log-init avoids filling large sparse wtmp/lastlog files on Ubuntu.
        (
            "useradd --system --no-log-init --create-home "
            + "--home-dir /var/lib/opensre-gateway --shell /usr/sbin/nologin opensre || true"
        ),
        "mkdir -p /var/lib/opensre-gateway/.opensre/gateway",
        "chown -R opensre:opensre /var/lib/opensre-gateway",
        "chmod 750 /var/lib/opensre-gateway",
        # Env file directory (populated at provision time by provision_gateway_via_ssm)
        "mkdir -p /etc/opensre && chmod 750 /etc/opensre && chown root:opensre /etc/opensre",
        # Download and install the pre-built binary.
        # OPENSRE_AUTO_LAUNCH=0 prevents the installer from trying to launch
        # the interactive onboarding wizard (a no-op in a non-TTY SSM session,
        # but explicit is safer).
        (
            f"OPENSRE_INSTALL_DIR={install_dir} OPENSRE_AUTO_LAUNCH=0 "
            f"bash -c 'curl -fsSL {install_url} | bash'"
        ),
        # Smoke-check: binary must be executable and respond to --help
        f"{_BINARY_INSTALL_DIR}/opensre --help > /dev/null",
        # Install the systemd unit (base64-encoded inline to avoid file transfers)
        (
            f"echo {shlex.quote(encoded_service)} | base64 -d "
            f"> /etc/systemd/system/{_SERVICE_NAME}.service"
        ),
        f"chmod 644 /etc/systemd/system/{_SERVICE_NAME}.service",
        "systemctl daemon-reload && systemctl enable opensre-gateway",
    ]


def _format_ssm_failure(result: dict[str, str]) -> str:
    """Return a readable SSM failure message including stdout and stderr."""
    status = result.get("status", "unknown")
    stdout = result.get("stdout", "").strip()
    stderr = result.get("stderr", "").strip()
    parts = [f"status={status}"]
    if stderr:
        parts.append(f"stderr={stderr[-2000:]}")
    if stdout:
        parts.append(f"stdout={stdout[-2000:]}")
    return ", ".join(parts)


# ── Deploy ─────────────────────────────────────────────────────────────────────


def install_on_new_server(
    *,
    env_vars: dict[str, str] | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, str]:
    """Launch a fresh EC2 instance and install the gateway via the curl installer.

    Installs the pre-built opensre binary (no git clone, no pip install, no
    Docker).  Takes ~3-5 minutes for the binary download.

    Args:
        env_vars: Gateway environment variables (TELEGRAM_BOT_TOKEN, etc.).
                  Defaults to reading the standard deploy keys from the process
                  environment.
        region:   AWS region (default: us-east-1).

    Returns:
        A dict of deployment outputs (InstanceId, PublicIpAddress, …).
    """
    stack_name = _direct_stack_name()
    start_time = time.time()

    print("=" * 60)
    print(f"Deploying {stack_name} (installer download, no prebuilt image)")
    print("=" * 60)
    print()

    _cleanup_existing(region=region)

    print("Creating IAM instance profile...")
    profile_info = create_instance_profile(
        role_name=f"{stack_name}-role",
        profile_name=f"{stack_name}-profile",
        stack_name=stack_name,
        region=region,
        extra_policy_arns=[SSM_MANAGED_POLICY_ARN],
    )
    print(f"  - Profile: {profile_info['ProfileName']}")

    print("Looking up latest Ubuntu 22.04 LTS AMI...")
    base_ami = get_latest_ubuntu2204_ami(region)
    print(f"  - Base AMI: {base_ami}")

    print("Creating security group...")
    security_group_id = create_stack_security_group(stack_name, region=region)
    print(f"  - Security group: {security_group_id}")

    print(f"Launching EC2 instance ({INSTANCE_TYPE})...")
    instance = launch_instance(
        ami_id=base_ami,
        instance_profile_arn=profile_info["ProfileArn"],
        stack_name=stack_name,
        instance_type=INSTANCE_TYPE,
        root_device_name=EC2_UBUNTU_ROOT_DEVICE_NAME,
        security_group_ids=[security_group_id],
        region=region,
    )
    instance_id = instance["InstanceId"]
    print(f"  - Instance ID: {instance_id}")

    print("Waiting for instance to start...")
    running = wait_for_running(instance_id, region)
    public_ip = running["PublicIpAddress"]
    print(f"  - Public IP: {public_ip}")

    print("Waiting for SSM agent to register...")
    wait_for_ssm_registration(instance_id, region)
    print("  - SSM: Online")

    print("Installing opensre binary via curl installer...")
    commands = _build_curl_install_commands()
    result = run_ssm_shell_command(
        instance_id=instance_id,
        commands=commands,
        region=region,
        max_poll_attempts=_INSTALL_MAX_POLL_ATTEMPTS,
    )
    if result["status"] != "Success":
        raise RuntimeError(
            f"Install commands failed on {instance_id}: {_format_ssm_failure(result)}"
        )
    print("  - Install: OK")

    print("Provisioning gateway (writing env file, starting service)...")
    provision_gateway_via_ssm(instance_id, env_vars=env_vars, region=region)
    print("  - Provision: OK")

    print("Waiting for gateway service to become ready...")
    wait_for_gateway_ready(instance_id, region=region)
    print("  - Gateway: Ready")

    outputs: dict[str, str] = {
        "StackName": stack_name,
        "InstanceId": instance_id,
        "PublicIpAddress": public_ip,
        "SecurityGroupId": security_group_id,
        "ProfileName": profile_info["ProfileName"],
        "RoleName": profile_info["RoleName"],
        "BaseAmiId": base_ami,
    }
    _save_outputs(outputs)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Direct deploy completed in {elapsed:.1f}s")
    print("=" * 60)
    print()
    for key, value in outputs.items():
        print(f"  {key}: {value}")

    return outputs


# ── Destroy ────────────────────────────────────────────────────────────────────


def destroy_installed_server(*, region: str = DEFAULT_REGION) -> dict[str, list[str]]:
    """Terminate the direct-deploy EC2 instance and clean up IAM resources."""
    stack_name = _direct_stack_name()
    start_time = time.time()
    print("=" * 60)
    print(f"Destroying {stack_name} infrastructure")
    print("=" * 60)
    print()

    results: dict[str, list[str]] = {"deleted": [], "failed": []}

    try:
        outputs = _load_outputs()
    except FileNotFoundError:
        print("No outputs file found — attempting cleanup by known names.")
        outputs = {}

    instance_id = outputs.get("InstanceId", "")
    security_group_id = outputs.get("SecurityGroupId", "")
    profile_name = outputs.get("ProfileName", f"{stack_name}-profile")
    role_name = outputs.get("RoleName", f"{stack_name}-role")

    if instance_id:
        print(f"Terminating EC2 instance {instance_id}...")
        try:
            terminate_instance(instance_id, region)
            results["deleted"].append(f"ec2-instance:{instance_id}")
            print("  - Instance terminated")
        except ClientError as e:
            results["failed"].append(f"ec2-instance:{instance_id} - {e}")
            print(f"  - Failed: {e}")

    if security_group_id:
        print(f"Deleting security group {security_group_id}...")
        try:
            delete_stack_security_group(security_group_id, region=region)
            results["deleted"].append(f"security-group:{security_group_id}")
            print("  - Security group deleted")
        except ClientError as e:
            results["failed"].append(f"security-group:{security_group_id} - {e}")
            print(f"  - Failed: {e}")

    print(f"Deleting IAM profile {profile_name} and role {role_name}...")
    try:
        delete_instance_profile(profile_name, role_name, region)
        results["deleted"].append(f"instance-profile:{profile_name}")
        results["deleted"].append(f"iam-role:{role_name}")
        print("  - Profile and role deleted")
    except ClientError as e:
        results["failed"].append(f"iam:{profile_name}/{role_name} - {e}")
        print(f"  - Failed: {e}")

    _delete_outputs()

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Destroy completed in {elapsed:.1f}s")
    print("=" * 60)

    if results["deleted"]:
        print(f"\nDeleted {len(results['deleted'])} resources:")
        for r in results["deleted"]:
            print(f"  - {r}")

    if results["failed"]:
        print(f"\nFailed to delete {len(results['failed'])} resources:")
        for r in results["failed"]:
            print(f"  - {r}")

    return results


# ── Internal helpers ───────────────────────────────────────────────────────────


def _cleanup_existing(*, region: str = DEFAULT_REGION) -> bool:
    """Destroy any prior direct-deploy stack before launching a new one.

    Returns True when cleanup ran.
    """
    stack_name = _direct_stack_name()
    has_outputs = _outputs_exist()
    instance_ids = find_stack_instance_ids(stack_name, region=region)

    if not has_outputs and not instance_ids:
        return False

    print("=" * 60)
    print("Existing direct-deploy stack detected — destroying previous stack")
    if instance_ids:
        print(f"  Active instances: {', '.join(instance_ids)}")
    print("=" * 60)
    print()

    for instance_id in instance_ids:
        print(f"Terminating stack instance {instance_id}...")
        terminate_instance(instance_id, region)

    # Always run destroy_installed_server so IAM profile/role are cleaned up even when
    # the outputs file is missing.  destroy_installed_server() falls back to derived
    # names ({stack_name}-profile / {stack_name}-role) when no outputs file
    # exists, so orphaned IAM resources are never left behind.
    destroy_installed_server(region=region)

    print()
    return True


__all__ = ["install_on_new_server", "destroy_installed_server"]
