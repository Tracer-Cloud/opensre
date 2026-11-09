#!/usr/bin/env python3
"""Deploy and destroy the OpenSRE messaging gateway on EC2 using a custom AMI."""

from __future__ import annotations

import argparse
import os
import time

from botocore.exceptions import ClientError

from config.constants.llm import ANTHROPIC_API_KEY_ENV, LLM_PROVIDER_ENV, OPENAI_API_KEY_ENV
from config.constants.telegram import TELEGRAM_BOT_TOKEN_ENV
from config.llm_auth.provider_catalog import require_provider_spec
from infrastructure.deployment.ec2.config import (
    DEFAULT_REGION,
    GATEWAY_AMI_DESTROY_PURGE_ENV,
    GATEWAY_AMI_ID_ENV,
    INSTANCE_TYPE,
    SSM_MANAGED_POLICY_ARN,
)
from infrastructure.deployment.ec2.ec2 import (
    create_instance_profile,
    create_stack_security_group,
    delete_instance_profile,
    delete_stack_security_group,
    deregister_image,
    find_stack_instance_ids,
    launch_instance,
    terminate_instance,
    wait_for_running,
)
from infrastructure.deployment.ec2.prep_ec2_deployment import (
    run_lifecycle_main,
    validate_deploy_env,
)
from infrastructure.deployment.ec2.ssm import wait_for_ssm_registration
from infrastructure.deployment.ec2.telegram_gateway.build_server_image import build_server_image
from infrastructure.deployment.ec2.telegram_gateway.install_on_new_server import (
    destroy_installed_server,
    install_on_new_server,
)
from infrastructure.deployment.ec2.telegram_gateway.provision import (
    provision_gateway_via_ssm,
    wait_for_gateway_ready,
)
from infrastructure.deployment.ec2.telegram_gateway.stack import (
    ami_id_exists,
    delete_outputs,
    get_stack,
    load_ami_id,
    load_outputs,
    outputs_exists,
    save_outputs,
)

REGION = DEFAULT_REGION

_EXTRA_ENV_KEYS_ENV = "OPENSRE_DEPLOY_EXTRA_ENV_KEYS"

# SLACK_* is intentionally absent: Slack is deployed and operated separately,
# not from this repo. Socket Mode is single-consumer — an EC2 gateway holding
# Slack tokens would compete with the primary Slack gateway for events.


def _hosted_model_env_keys() -> tuple[str, ...]:
    """OpenAI/Anthropic model env names the runtime actually reads.

    ``ANTHROPIC_MODEL`` / ``OPENAI_MODEL`` remain as legacy aliases. The current
    names (``*_REASONING_MODEL``, ``*_TOOLCALL_MODEL``, ``*_CLASSIFICATION_MODEL``)
    must ship too or a deploy silently falls back to catalog defaults.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for provider in ("openai", "anthropic"):
        spec = require_provider_spec(provider)
        for key in (
            spec.model_env,
            spec.legacy_model_env,
            spec.toolcall_model_env,
            spec.classification_model_env,
        ):
            if key and key not in seen:
                seen.add(key)
                keys.append(key)
    return tuple(keys)


_CONTAINER_ENV_KEYS = (
    TELEGRAM_BOT_TOKEN_ENV,
    "TELEGRAM_ALLOWED_USERS",
    LLM_PROVIDER_ENV,
    OPENAI_API_KEY_ENV,
    ANTHROPIC_API_KEY_ENV,
    *_hosted_model_env_keys(),
)

_ABORT_IF_EXISTS_ENV = "OPENSRE_DEPLOY_ABORT_IF_EXISTS"


def _extra_env_keys() -> tuple[str, ...]:
    """Additional env keys to ship, from OPENSRE_DEPLOY_EXTRA_ENV_KEYS (CSV).

    Lets a deployment carry integration credentials (Grafana, Datadog, …) so
    the deployed agent's tools become available, without hardcoding every
    vendor's variables here.
    """
    raw = os.getenv(_EXTRA_ENV_KEYS_ENV, "")
    return tuple(key.strip() for key in raw.split(",") if key.strip())


def _collect_deploy_env_vars() -> dict[str, str]:
    env_vars: dict[str, str] = {}
    for key in (*_CONTAINER_ENV_KEYS, *_extra_env_keys()):
        val = os.getenv(key)
        if val:
            env_vars[key] = val
    return env_vars


def _abort_if_exists_enabled() -> bool:
    return os.getenv(_ABORT_IF_EXISTS_ENV, "").strip().lower() in {"1", "true", "yes"}


def _purge_ami_enabled() -> bool:
    return os.getenv(GATEWAY_AMI_DESTROY_PURGE_ENV, "").strip().lower() in {"1", "true", "yes"}


def _resolve_ami_id() -> str:
    """Return the AMI id to deploy.

    Resolution order:
      1. ``OPENSRE_GATEWAY_AMI_ID`` environment variable.
      2. AMI id saved on disk by the last ``make build-gateway-image`` run.

    Raises:
        RuntimeError: When neither source is available.
    """
    ami_id = os.getenv(GATEWAY_AMI_ID_ENV, "").strip()
    if ami_id:
        return ami_id
    if ami_id_exists():
        return load_ami_id()
    raise RuntimeError(
        "No pre-built gateway AMI found. Run `make build-gateway-image` first to build one, "
        f"or set {GATEWAY_AMI_ID_ENV} to an existing AMI id.\n\n"
        "  Quick start:\n"
        "    make build-gateway-image   # build once, saves the image id locally\n"
        "    make deploy-gateway # launch from saved AMI (fast)\n\n"
        "  Or in one step:\n"
        f"    {GATEWAY_AMI_ID_ENV}=<ami-id> make deploy-gateway"
    )


def cleanup_existing_deployment(*, region: str = DEFAULT_REGION) -> bool:
    """Destroy a prior gateway deployment when outputs or tagged instances exist.

    Returns True when cleanup ran.
    """
    stack = get_stack()
    has_outputs = outputs_exists()
    instance_ids = find_stack_instance_ids(stack.stack_name, region=region)

    if not has_outputs and not instance_ids:
        return False

    if _abort_if_exists_enabled():
        raise RuntimeError(
            "Existing gateway deployment detected "
            f"({len(instance_ids)} active instance(s)). "
            "Run `make destroy-gateway` first, or unset OPENSRE_DEPLOY_ABORT_IF_EXISTS."
        )

    print("=" * 60)
    print("Existing gateway deployment detected — destroying previous stack")
    if instance_ids:
        print(f"  Active instances: {', '.join(instance_ids)}")
    print("=" * 60)
    print()

    for instance_id in instance_ids:
        print(f"Terminating stack instance {instance_id}...")
        terminate_instance(instance_id, region)

    if has_outputs or instance_ids:
        destroy()

    print()
    return True


def deploy() -> dict[str, str]:
    """Launch an EC2 instance from the pre-built gateway AMI and start the service."""
    validate_deploy_env()

    stack = get_stack()
    start_time = time.time()
    ami_id = _resolve_ami_id()

    print("=" * 60)
    print(f"Deploying {stack.stack_name} (gateway on one EC2 instance, systemd)")
    print("=" * 60)
    print()
    print(f"  AMI: {ami_id}")
    print()

    cleanup_existing_deployment(region=REGION)

    print("Creating IAM instance profile...")
    profile_info = create_instance_profile(
        role_name=f"{stack.stack_name}-role",
        profile_name=f"{stack.stack_name}-profile",
        stack_name=stack.stack_name,
        region=REGION,
        extra_policy_arns=[SSM_MANAGED_POLICY_ARN],
    )
    print(f"  - Profile: {profile_info['ProfileName']}")

    print("Creating security group...")
    security_group_id = create_stack_security_group(stack.stack_name, region=REGION)
    print(f"  - Security group: {security_group_id}")

    print("Launching EC2 instance from gateway AMI...")
    instance = launch_instance(
        ami_id=ami_id,
        instance_profile_arn=profile_info["ProfileArn"],
        stack_name=stack.stack_name,
        instance_type=INSTANCE_TYPE,
        security_group_ids=[security_group_id],
        region=REGION,
    )
    instance_id = instance["InstanceId"]
    print(f"  - Instance ID: {instance_id}")

    print("Waiting for instance to start...")
    running = wait_for_running(instance_id, REGION)
    public_ip = running["PublicIpAddress"]
    print(f"  - Public IP: {public_ip}")

    print("Waiting for SSM agent to register...")
    wait_for_ssm_registration(instance_id, REGION)
    print("  - SSM: Online")

    print("Provisioning gateway (writing env file, starting service)...")
    provision_gateway_via_ssm(
        instance_id,
        env_vars=_collect_deploy_env_vars(),
        region=REGION,
    )
    print("  - Provision: OK")

    print("Waiting for gateway service to become ready...")
    wait_for_gateway_ready(instance_id, region=REGION)
    print("  - Gateway: Ready")

    outputs: dict[str, str] = {
        "StackName": stack.stack_name,
        "InstanceId": instance_id,
        "PublicIpAddress": public_ip,
        "SecurityGroupId": security_group_id,
        "ProfileName": profile_info["ProfileName"],
        "RoleName": profile_info["RoleName"],
        "AmiId": ami_id,
    }

    save_outputs(outputs)

    elapsed = time.time() - start_time
    print()
    print("=" * 60)
    print(f"Deployment completed in {elapsed:.1f}s")
    print("=" * 60)
    print()
    for key, value in outputs.items():
        print(f"  {key}: {value}")

    return outputs


def destroy() -> dict[str, list[str]]:
    """Terminate the EC2 instance and clean up EC2/IAM resources.

    The custom AMI is kept by default so that a subsequent deploy does not need
    a full image rebuild. Set ``OPENSRE_GATEWAY_DESTROY_PURGE_AMI=1`` to also
    deregister the AMI and delete its backing snapshot (e.g. for a full
    account cleanup).
    """
    stack = get_stack()
    start_time = time.time()
    print("=" * 60)
    print(f"Destroying {stack.stack_name} infrastructure")
    print("=" * 60)
    print()

    results: dict[str, list[str]] = {"deleted": [], "failed": []}

    try:
        outputs = load_outputs()
    except FileNotFoundError:
        print("No outputs file found — attempting cleanup by known names.")
        outputs = {}

    instance_id = outputs.get("InstanceId", "")
    security_group_id = outputs.get("SecurityGroupId", "")
    profile_name = outputs.get("ProfileName", f"{stack.stack_name}-profile")
    role_name = outputs.get("RoleName", f"{stack.stack_name}-role")
    saved_ami_id = outputs.get("AmiId", "")

    if instance_id:
        print(f"Terminating EC2 instance {instance_id}...")
        try:
            terminate_instance(instance_id, REGION)
            results["deleted"].append(f"ec2-instance:{instance_id}")
            print("  - Instance terminated")
        except ClientError as e:
            msg = f"ec2-instance:{instance_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    if security_group_id:
        print(f"Deleting security group {security_group_id}...")
        try:
            delete_stack_security_group(security_group_id, region=REGION)
            results["deleted"].append(f"security-group:{security_group_id}")
            print("  - Security group deleted")
        except ClientError as e:
            msg = f"security-group:{security_group_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")

    print(f"Deleting IAM profile {profile_name} and role {role_name}...")
    try:
        delete_instance_profile(profile_name, role_name, REGION)
        results["deleted"].append(f"instance-profile:{profile_name}")
        results["deleted"].append(f"iam-role:{role_name}")
        print("  - Profile and role deleted")
    except ClientError as e:
        msg = f"iam:{profile_name}/{role_name} - {e}"
        results["failed"].append(msg)
        print(f"  - Failed: {e}")

    if _purge_ami_enabled() and saved_ami_id:
        print(f"Deregistering gateway AMI {saved_ami_id} ({GATEWAY_AMI_DESTROY_PURGE_ENV}=1)...")
        try:
            deregister_image(saved_ami_id, region=REGION)
            results["deleted"].append(f"ami:{saved_ami_id}")
            print("  - AMI deregistered")
        except ClientError as e:
            msg = f"ami:{saved_ami_id} - {e}"
            results["failed"].append(msg)
            print(f"  - Failed: {e}")
    elif saved_ami_id:
        print(
            f"Keeping gateway AMI {saved_ami_id} "
            f"(set {GATEWAY_AMI_DESTROY_PURGE_ENV}=1 to also deregister it)."
        )

    delete_outputs()

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


def main() -> None:
    parser = argparse.ArgumentParser(description="OpenSRE gateway deployment lifecycle")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser(
        "build-server-image",
        help="Build a server image with the gateway installed (run once per code change)",
    )
    subparsers.add_parser("deploy", help="Start a gateway server from the prebuilt image")
    subparsers.add_parser("destroy", help="Tear down the server started from the image")
    subparsers.add_parser(
        "install-on-new-server",
        help="Start a new server and install the gateway on it, without building an image first",
    )
    subparsers.add_parser(
        "destroy-installed-server",
        help="Tear down the server created by install-on-new-server",
    )
    args = parser.parse_args()

    if args.command == "build-server-image":
        build_server_image(region=REGION)
    elif args.command == "deploy":
        deploy()
    elif args.command == "install-on-new-server":
        validate_deploy_env()
        install_on_new_server(env_vars=_collect_deploy_env_vars(), region=REGION)
    elif args.command == "destroy-installed-server":
        destroy_installed_server(region=REGION)
    else:
        destroy()


if __name__ == "__main__":
    run_lifecycle_main(main)
