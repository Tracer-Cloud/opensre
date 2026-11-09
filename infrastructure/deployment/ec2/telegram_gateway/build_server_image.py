"""Build a reusable server image with the gateway already installed.

Runs once per code change: it starts a temporary server, installs the gateway on
it, saves the result as an image, and throws the temporary server away. Servers
started from that image are ready immediately.

The alternative is :mod:`infrastructure.deployment.ec2.telegram_gateway.install_on_new_server`,
which installs onto each new server instead of reusing an image.
"""

from __future__ import annotations

import base64
import logging
import os
import shlex
import subprocess
import time
from pathlib import Path

from infrastructure.deployment.ec2.config import (
    DEFAULT_REGION,
    GATEWAY_AMI_GIT_REF_ENV,
    GATEWAY_AMI_NAME_PREFIX,
    GATEWAY_BUILDER_INSTANCE_TYPE,
    SSM_MANAGED_POLICY_ARN,
)
from infrastructure.deployment.ec2.ec2 import (
    create_image_from_instance,
    create_instance_profile,
    delete_instance_profile,
    get_latest_al2023_ami,
    launch_instance,
    terminate_instance,
    wait_for_running,
)
from infrastructure.deployment.ec2.ssm import run_ssm_shell_command, wait_for_ssm_registration
from infrastructure.deployment.ec2.telegram_gateway.stack import (
    GatewayStack,
    get_stack,
    save_ami_id,
)

logger = logging.getLogger(__name__)

_SERVICE_FILE = Path(__file__).parent / "systemd" / "opensre-gateway.service"
_OPENSRE_REPO = "Tracer-Cloud/opensre"

# These suffixes name live AWS IAM resources for the temporary builder server.
# Renaming them would orphan anything already deployed, so they keep the older
# "bake" wording even though the module no longer uses it.
_BUILDER_ROLE_SUFFIX = "-bake-role"
_BUILDER_PROFILE_SUFFIX = "-bake-profile"


def _resolve_git_ref() -> str:
    """Return the git ref to install into the image.

    Resolution order:
      1. ``OPENSRE_GATEWAY_GIT_REF`` environment variable.
      2. ``git rev-parse HEAD`` in the local repo.
      3. Falls back to ``"main"`` if git is not available.
    """
    env_ref = os.getenv(GATEWAY_AMI_GIT_REF_ENV, "").strip()
    if env_ref:
        return env_ref
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except Exception:  # noqa: BLE001
        logger.warning("Could not determine git HEAD; falling back to 'main'")
        return "main"


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


def _build_install_commands(git_ref: str) -> list[str]:
    """Return the list of SSM shell commands that set up the gateway on the builder instance.

    All setup is inlined here — no external bash script is downloaded or executed.
    The systemd unit is base64-encoded from the local repo and decoded on the instance.
    The opensre package is installed via pip's git+ protocol from GitHub.
    """
    service_content = _SERVICE_FILE.read_text(encoding="utf-8")
    encoded_service = base64.b64encode(service_content.encode()).decode("ascii")
    # shlex.quote on the ref guards against shell injection for unusual ref names
    install_url = f"git+https://github.com/{_OPENSRE_REPO}.git@{shlex.quote(git_ref)}"

    return [
        "set -euo pipefail",
        # AL2023 ships curl-minimal; do not install the full curl package (conflicts)
        "dnf install -y python3.12 python3.12-pip git",
        # create system user and persistent data dirs
        (
            "id opensre &>/dev/null || useradd --system --create-home "
            + "--home-dir /var/lib/opensre-gateway --shell /sbin/nologin opensre"
        ),
        "mkdir -p /var/lib/opensre-gateway/.opensre/gateway",
        "chown -R opensre:opensre /var/lib/opensre-gateway",
        "chmod 750 /var/lib/opensre-gateway",
        # install opensre into an isolated venv
        "python3.12 -m venv /opt/opensre/.venv",
        f"/opt/opensre/.venv/bin/pip install --quiet {install_url}",
        # smoke-check: verify the CLI is importable
        "/opt/opensre/.venv/bin/opensre --help > /dev/null",
        # env file directory (populated at deploy time, not while building the image)
        "mkdir -p /etc/opensre && chmod 750 /etc/opensre && chown root:opensre /etc/opensre",
        # install systemd unit (base64-inlined from local repo)
        (
            f"echo {shlex.quote(encoded_service)} | base64 -d "
            "> /etc/systemd/system/opensre-gateway.service"
        ),
        "chmod 644 /etc/systemd/system/opensre-gateway.service",
        "systemctl daemon-reload && systemctl enable opensre-gateway",
    ]


def build_server_image(
    *,
    region: str = DEFAULT_REGION,
    ami_id_path: object = None,
) -> str:
    """Launch a builder instance, run the install commands, snapshot it into an AMI.

    Returns the new AMI id.
    """
    stack: GatewayStack = get_stack()
    git_ref = _resolve_git_ref()
    timestamp = int(time.time())
    ami_name = f"{GATEWAY_AMI_NAME_PREFIX}-{git_ref[:12]}-{timestamp}"

    print("=" * 60)
    print(f"Baking gateway AMI for {stack.stack_name}")
    print("=" * 60)
    print()
    print(f"  Git ref : {git_ref}")
    print(f"  AMI name: {ami_name}")
    print()

    builder_role = f"{stack.stack_name}{_BUILDER_ROLE_SUFFIX}"
    builder_profile = f"{stack.stack_name}{_BUILDER_PROFILE_SUFFIX}"

    print("Creating builder IAM instance profile...")
    profile_info = create_instance_profile(
        role_name=builder_role,
        profile_name=builder_profile,
        stack_name=stack.stack_name,
        region=region,
        extra_policy_arns=[SSM_MANAGED_POLICY_ARN],
    )
    print(f"  - Profile: {profile_info['ProfileName']}")

    print("Looking up latest Amazon Linux 2023 AMI...")
    base_ami = get_latest_al2023_ami(region)
    print(f"  - Base AMI: {base_ami}")

    print(f"Launching builder instance ({GATEWAY_BUILDER_INSTANCE_TYPE})...")
    instance = launch_instance(
        ami_id=base_ami,
        instance_profile_arn=profile_info["ProfileArn"],
        stack_name=stack.stack_name,
        instance_type=GATEWAY_BUILDER_INSTANCE_TYPE,
        region=region,
    )
    instance_id = instance["InstanceId"]
    print(f"  - Instance ID: {instance_id}")

    try:
        print("Waiting for instance to start...")
        wait_for_running(instance_id, region)
        print("  - Running")

        print("Waiting for SSM agent to register...")
        wait_for_ssm_registration(instance_id, region)
        print("  - SSM: Online")

        print("Running install commands on builder instance (may take several minutes)...")
        commands = _build_install_commands(git_ref)
        result = run_ssm_shell_command(
            instance_id=instance_id,
            commands=commands,
            region=region,
            # Use generous poll limits — pip install can take a while
            max_poll_attempts=120,
        )
        if result["status"] != "Success":
            raise RuntimeError(
                f"Install commands failed on {instance_id}: {_format_ssm_failure(result)}"
            )
        print("  - Install: OK")

        print("Creating AMI (AWS will stop/snapshot/restart the instance)...")
        image_id = create_image_from_instance(
            instance_id=instance_id,
            name=ami_name,
            stack_name=stack.stack_name,
            region=region,
        )
        print(f"  - AMI: {image_id}")

    finally:
        print(f"Terminating builder instance {instance_id}...")
        try:
            terminate_instance(instance_id, region)
            print("  - Terminated")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to terminate builder instance %s: %s", instance_id, exc)

        print(f"Deleting builder IAM profile {builder_profile}...")
        try:
            delete_instance_profile(builder_profile, builder_role, region)
            print("  - Deleted")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to delete builder profile %s: %s", builder_profile, exc)

    kwargs = {"path": ami_id_path} if ami_id_path is not None else {}
    save_ami_id(image_id, **kwargs)  # type: ignore[arg-type]

    print()
    print("=" * 60)
    print("Server image build complete")
    print(f"  AMI id : {image_id}")
    print(f"  Git ref: {git_ref}")
    print("  Run `make deploy-gateway` to launch an instance from this AMI.")
    print("=" * 60)
    print()
    return image_id
