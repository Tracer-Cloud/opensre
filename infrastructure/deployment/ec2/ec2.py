"""EC2 instance provisioning for OpenSRE deployments."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time

from botocore.exceptions import ClientError

from infrastructure.deployment.ec2.client import get_boto3_client, get_standard_tags
from infrastructure.deployment.ec2.config import (
    AL2023_AMI_SSM_PARAMETER,
    BEDROCK_POLICY_ARN,
    DEFAULT_REGION,
    EC2_INSTANCE_ROLE_DESCRIPTION,
    EC2_ROOT_DEVICE_NAME,
    EC2_VOLUME_SIZE_GB,
    EC2_VOLUME_TYPE,
    EC2_WAITER_DELAY_SECONDS,
    EC2_WAITER_MAX_ATTEMPTS,
    ECR_READ_POLICY_ARN,
    IAM_PROFILE_PROPAGATION_SECONDS,
    INSTANCE_TYPE,
    STACK_TAG_KEY,
    WEB_API_INGRESS_CIDR_DEFAULT,
    WEB_API_INGRESS_CIDR_ENV,
    WEB_API_PORT,
)

ACTIVE_EC2_INSTANCE_STATES = ("pending", "running", "stopping")

logger = logging.getLogger(__name__)


def get_latest_al2023_ami(region: str = DEFAULT_REGION) -> str:
    """Find the latest Amazon Linux 2023 x86_64 AMI via SSM parameter."""
    ssm = get_boto3_client("ssm", region)
    resp = ssm.get_parameter(Name=AL2023_AMI_SSM_PARAMETER)
    return str(resp["Parameter"]["Value"])


def get_latest_ubuntu2204_ami(region: str = DEFAULT_REGION) -> str:
    """Find the latest Ubuntu 22.04 LTS (Jammy) x86_64 AMI via Canonical's SSM parameter.

    Ubuntu 22.04 ships glibc 2.35, which satisfies the pre-built opensre PyInstaller
    binary's runtime requirement.  Amazon Linux 2023 only ships glibc 2.34 and will
    fail with a ``GLIBC_2.35 not found`` error at binary launch time.
    """
    from infrastructure.deployment.ec2.config import UBUNTU2204_AMI_SSM_PARAMETER

    ssm = get_boto3_client("ssm", region)
    resp = ssm.get_parameter(Name=UBUNTU2204_AMI_SSM_PARAMETER)
    return str(resp["Parameter"]["Value"])


def create_instance_profile(
    role_name: str,
    profile_name: str,
    stack_name: str,
    region: str = DEFAULT_REGION,
    extra_policy_arns: list[str] | None = None,
) -> dict[str, str]:
    """Create an IAM instance profile with EC2 trust, ECR read, and Bedrock policies.

    Passes ``extra_policy_arns`` for additional managed policies (e.g. SSM access).
    Returns a dict with ProfileName, ProfileArn, and RoleName.
    """
    iam = get_boto3_client("iam", region)
    tags = get_standard_tags(stack_name)

    ec2_trust_policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Effect": "Allow",
                "Principal": {"Service": "ec2.amazonaws.com"},
                "Action": "sts:AssumeRole",
            }
        ],
    }

    try:
        resp = iam.create_role(
            RoleName=role_name,
            AssumeRolePolicyDocument=json.dumps(ec2_trust_policy),
            Description=EC2_INSTANCE_ROLE_DESCRIPTION,
            Tags=tags,
        )
        role_arn = resp["Role"]["Arn"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "EntityAlreadyExists":
            resp = iam.get_role(RoleName=role_name)
            role_arn = resp["Role"]["Arn"]
        else:
            raise

    logger.info("IAM role ready: %s (%s)", role_name, role_arn)

    try:
        iam.create_instance_profile(InstanceProfileName=profile_name, Tags=tags)
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise

    try:
        iam.add_role_to_instance_profile(InstanceProfileName=profile_name, RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "LimitExceeded":
            raise

    with contextlib.suppress(ClientError):
        iam.attach_role_policy(RoleName=role_name, PolicyArn=ECR_READ_POLICY_ARN)

    with contextlib.suppress(ClientError):
        iam.attach_role_policy(RoleName=role_name, PolicyArn=BEDROCK_POLICY_ARN)

    for arn in extra_policy_arns or []:
        with contextlib.suppress(ClientError):
            iam.attach_role_policy(RoleName=role_name, PolicyArn=arn)

    if IAM_PROFILE_PROPAGATION_SECONDS > 0:
        time.sleep(IAM_PROFILE_PROPAGATION_SECONDS)

    resp = iam.get_instance_profile(InstanceProfileName=profile_name)
    return {
        "ProfileName": profile_name,
        "ProfileArn": resp["InstanceProfile"]["Arn"],
        "RoleName": role_name,
    }


def delete_instance_profile(
    profile_name: str,
    role_name: str,
    region: str = DEFAULT_REGION,
) -> None:
    """Delete an IAM instance profile and its associated role."""
    iam = get_boto3_client("iam", region)

    try:
        iam.remove_role_from_instance_profile(InstanceProfileName=profile_name, RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to remove role from profile: %s", e)

    try:
        iam.delete_instance_profile(InstanceProfileName=profile_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to delete instance profile: %s", e)

    try:
        attached = iam.list_attached_role_policies(RoleName=role_name)
        for policy in attached.get("AttachedPolicies", []):
            iam.detach_role_policy(RoleName=role_name, PolicyArn=policy["PolicyArn"])
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to detach policies: %s", e)

    try:
        inline = iam.list_role_policies(RoleName=role_name)
        for policy_name in inline.get("PolicyNames", []):
            iam.delete_role_policy(RoleName=role_name, PolicyName=policy_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            logger.warning("Failed to delete inline policies: %s", e)

    try:
        iam.delete_role(RoleName=role_name)
    except ClientError as e:
        if e.response["Error"]["Code"] != "NoSuchEntity":
            raise


def stack_security_group_name(stack_name: str) -> str:
    """Return the deterministic security group name for a deployment stack."""
    return f"{stack_name}-sg"


def web_api_ingress_cidr() -> str:
    """Return the CIDR allowed to reach the web API port."""
    return os.getenv(WEB_API_INGRESS_CIDR_ENV, WEB_API_INGRESS_CIDR_DEFAULT).strip()


def get_default_vpc_id(*, region: str = DEFAULT_REGION) -> str:
    """Return the account default VPC id.

    Raises:
        ClientError: When the region has no default VPC (``DefaultVpcNotFound``).
    """
    ec2 = get_boto3_client("ec2", region)
    resp = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])
    vpcs = resp.get("Vpcs", [])
    if not vpcs:
        raise ClientError(
            {
                "Error": {
                    "Code": "DefaultVpcNotFound",
                    "Message": (
                        f"No default VPC found in {region}. Create a default VPC or "
                        "deploy into an account/region with a default VPC."
                    ),
                }
            },
            "DescribeVpcs",
        )
    return str(vpcs[0]["VpcId"])


def _web_api_ingress_permission(
    *,
    port: int,
    cidr: str,
    description: str,
) -> dict[str, object]:
    return {
        "IpProtocol": "tcp",
        "FromPort": port,
        "ToPort": port,
        "IpRanges": [{"CidrIp": cidr, "Description": description}],
    }


def _revoke_stale_web_api_ingress(
    *,
    group_id: str,
    port: int,
    desired_cidr: str,
    region: str = DEFAULT_REGION,
) -> bool:
    """Revoke web API ingress CIDRs that do not match ``desired_cidr``.

    Returns True when ``desired_cidr`` is already authorized on ``port``.
    """
    ec2 = get_boto3_client("ec2", region)
    response = ec2.describe_security_groups(GroupIds=[group_id])
    security_groups = response.get("SecurityGroups", [])
    if not security_groups:
        return False
    desired_present = False

    for permission in security_groups[0].get("IpPermissions", []):
        if permission.get("IpProtocol") != "tcp":
            continue
        if permission.get("FromPort") != port or permission.get("ToPort") != port:
            continue

        ip_ranges = permission.get("IpRanges") or []
        stale_ranges = [
            ip_range for ip_range in ip_ranges if ip_range.get("CidrIp") != desired_cidr
        ]
        if any(ip_range.get("CidrIp") == desired_cidr for ip_range in ip_ranges):
            desired_present = True
        if not stale_ranges:
            continue

        try:
            ec2.revoke_security_group_ingress(
                GroupId=group_id,
                IpPermissions=[
                    {
                        "IpProtocol": "tcp",
                        "FromPort": port,
                        "ToPort": port,
                        "IpRanges": stale_ranges,
                    }
                ],
            )
        except ClientError as e:
            if e.response["Error"]["Code"] != "InvalidPermission.NotFound":
                raise

    return desired_present


def _authorize_tcp_ingress(
    *,
    group_id: str,
    port: int,
    cidr: str,
    description: str,
    region: str = DEFAULT_REGION,
) -> None:
    ec2 = get_boto3_client("ec2", region)
    try:
        ec2.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[
                _web_api_ingress_permission(port=port, cidr=cidr, description=description)
            ],
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise


def _sync_web_api_ingress(
    *,
    group_id: str,
    cidr: str,
    region: str = DEFAULT_REGION,
) -> None:
    """Ensure only ``cidr`` can reach the web API port on ``group_id``."""
    description = f"OpenSRE web API port {WEB_API_PORT}"
    desired_present = _revoke_stale_web_api_ingress(
        group_id=group_id,
        port=WEB_API_PORT,
        desired_cidr=cidr,
        region=region,
    )
    if desired_present:
        return
    _authorize_tcp_ingress(
        group_id=group_id,
        port=WEB_API_PORT,
        cidr=cidr,
        description=description,
        region=region,
    )


def create_stack_security_group(
    stack_name: str,
    *,
    region: str = DEFAULT_REGION,
) -> str:
    """Create (or reuse) a stack security group with inbound TCP on the web API port.

    Returns the security group id (e.g. ``sg-0abc123...``).
    """
    ec2 = get_boto3_client("ec2", region)
    vpc_id = get_default_vpc_id(region=region)
    group_name = stack_security_group_name(stack_name)
    tags = get_standard_tags(stack_name)
    tags.append({"Key": "Name", "Value": group_name})

    response = ec2.describe_security_groups(
        Filters=[
            {"Name": "group-name", "Values": [group_name]},
            {"Name": "vpc-id", "Values": [vpc_id]},
        ]
    )
    existing = response.get("SecurityGroups", [])
    if existing:
        group_id = str(existing[0]["GroupId"])
    else:
        created = ec2.create_security_group(
            GroupName=group_name,
            Description=f"OpenSRE deployment security group for {stack_name}",
            VpcId=vpc_id,
            TagSpecifications=[{"ResourceType": "security-group", "Tags": tags}],
        )
        group_id = str(created["GroupId"])
        logger.info("Created security group %s for stack %s", group_id, stack_name)

    cidr = web_api_ingress_cidr()
    _sync_web_api_ingress(group_id=group_id, cidr=cidr, region=region)
    return group_id


def delete_stack_security_group(
    security_group_id: str,
    *,
    region: str = DEFAULT_REGION,
) -> None:
    """Delete a stack security group. Tolerates missing groups for idempotent destroy."""
    if not security_group_id:
        return
    ec2 = get_boto3_client("ec2", region)
    try:
        ec2.delete_security_group(GroupId=security_group_id)
        logger.info("Deleted security group %s", security_group_id)
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidGroup.NotFound":
            raise


def find_stack_instance_ids(
    stack_name: str,
    *,
    region: str = DEFAULT_REGION,
) -> list[str]:
    """Return instance IDs tagged for the stack that are still active."""
    ec2 = get_boto3_client("ec2", region)
    response = ec2.describe_instances(
        Filters=[
            {"Name": f"tag:{STACK_TAG_KEY}", "Values": [stack_name]},
            {"Name": "instance-state-name", "Values": list(ACTIVE_EC2_INSTANCE_STATES)},
        ]
    )

    instance_ids: list[str] = []
    for reservation in response.get("Reservations", []):
        for instance in reservation.get("Instances", []):
            instance_id = instance.get("InstanceId")
            if instance_id:
                instance_ids.append(str(instance_id))
    return sorted(instance_ids)


def launch_instance(
    ami_id: str,
    instance_profile_arn: str,
    stack_name: str,
    *,
    user_data: str | None = None,
    instance_type: str = INSTANCE_TYPE,
    root_device_name: str = EC2_ROOT_DEVICE_NAME,
    security_group_ids: list[str] | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, str]:
    """Launch an EC2 instance in the default VPC and return its InstanceId.

    When ``security_group_ids`` is omitted, AWS assigns the default VPC security
    group. Pass a stack security group from :func:`create_stack_security_group` to
    expose the web API port publicly.

    ``root_device_name`` must match the AMI root block device (``/dev/xvda`` for
    Amazon Linux 2023, ``/dev/sda1`` for Ubuntu official AMIs) or the requested
    EBS volume size is silently ignored.
    """
    ec2 = get_boto3_client("ec2", region)
    tags = get_standard_tags(stack_name)
    tags.append({"Key": "Name", "Value": f"{stack_name}-instance"})

    launch_kwargs: dict = {
        "ImageId": ami_id,
        "InstanceType": instance_type,
        "MinCount": 1,
        "MaxCount": 1,
        "IamInstanceProfile": {"Arn": instance_profile_arn},
        "TagSpecifications": [{"ResourceType": "instance", "Tags": tags}],
        "BlockDeviceMappings": [
            {
                "DeviceName": root_device_name,
                "Ebs": {"VolumeSize": EC2_VOLUME_SIZE_GB, "VolumeType": EC2_VOLUME_TYPE},
            }
        ],
    }
    if user_data:
        launch_kwargs["UserData"] = user_data
    if security_group_ids:
        launch_kwargs["SecurityGroupIds"] = security_group_ids
    key_name = os.getenv("EC2_KEY_NAME")
    if key_name:
        launch_kwargs["KeyName"] = key_name
    resp = ec2.run_instances(**launch_kwargs)

    instance_id = resp["Instances"][0]["InstanceId"]
    logger.info("Launched EC2 instance: %s", instance_id)
    return {"InstanceId": instance_id}


def wait_for_running(instance_id: str, region: str = DEFAULT_REGION) -> dict[str, str]:
    """Wait for an EC2 instance to reach the running state and return its public IP."""
    ec2 = get_boto3_client("ec2", region)
    waiter = ec2.get_waiter("instance_running")
    waiter.wait(
        InstanceIds=[instance_id],
        WaiterConfig={"Delay": EC2_WAITER_DELAY_SECONDS, "MaxAttempts": EC2_WAITER_MAX_ATTEMPTS},
    )

    resp = ec2.describe_instances(InstanceIds=[instance_id])
    instance = resp["Reservations"][0]["Instances"][0]
    public_ip = instance.get("PublicIpAddress", "")

    logger.info("Instance %s running at %s", instance_id, public_ip)
    return {"InstanceId": instance_id, "PublicIpAddress": public_ip}


def create_image_from_instance(
    instance_id: str,
    name: str,
    stack_name: str,
    *,
    region: str = DEFAULT_REGION,
) -> str:
    """Create an AMI from a running instance and wait until it is available.

    AWS stops the instance, takes the snapshot, then restarts it.  The caller
    is responsible for terminating the builder instance afterward.

    Returns the new AMI id (e.g. ``ami-0abc123...``).
    """
    from infrastructure.deployment.ec2.config import (
        GATEWAY_AMI_WAITER_DELAY_SECONDS,
        GATEWAY_AMI_WAITER_MAX_ATTEMPTS,
    )

    ec2 = get_boto3_client("ec2", region)
    tags = get_standard_tags(stack_name)
    tags.append({"Key": "Name", "Value": name})

    resp = ec2.create_image(
        InstanceId=instance_id,
        Name=name,
        NoReboot=False,
        TagSpecifications=[{"ResourceType": "image", "Tags": tags}],
    )
    image_id = str(resp["ImageId"])
    logger.info("Creating AMI %s from instance %s", image_id, instance_id)

    waiter = ec2.get_waiter("image_available")
    waiter.wait(
        ImageIds=[image_id],
        WaiterConfig={
            "Delay": GATEWAY_AMI_WAITER_DELAY_SECONDS,
            "MaxAttempts": GATEWAY_AMI_WAITER_MAX_ATTEMPTS,
        },
    )
    logger.info("AMI %s is now available", image_id)
    return image_id


def deregister_image(image_id: str, *, region: str = DEFAULT_REGION) -> None:
    """Deregister an AMI and delete its backing EBS snapshot.

    Tolerates ``InvalidAMIID.NotFound`` so destroy() is idempotent.
    """
    ec2 = get_boto3_client("ec2", region)

    try:
        resp = ec2.describe_images(ImageIds=[image_id])
        images = resp.get("Images", [])
        snapshot_ids: list[str] = []
        for image in images:
            for mapping in image.get("BlockDeviceMappings", []):
                ebs = mapping.get("Ebs", {})
                if ebs.get("SnapshotId"):
                    snapshot_ids.append(str(ebs["SnapshotId"]))

        ec2.deregister_image(ImageId=image_id)
        logger.info("Deregistered AMI %s", image_id)

        for snapshot_id in snapshot_ids:
            try:
                ec2.delete_snapshot(SnapshotId=snapshot_id)
                logger.info("Deleted snapshot %s", snapshot_id)
            except ClientError as e:
                if "InvalidSnapshot.NotFound" not in str(e):
                    logger.warning("Failed to delete snapshot %s: %s", snapshot_id, e)
    except ClientError as e:
        if "InvalidAMIID.NotFound" not in str(e):
            raise
        logger.warning("AMI %s already gone", image_id)


def terminate_instance(instance_id: str, region: str = DEFAULT_REGION) -> None:
    """Terminate an EC2 instance and wait for termination."""
    ec2 = get_boto3_client("ec2", region)
    try:
        ec2.terminate_instances(InstanceIds=[instance_id])
        waiter = ec2.get_waiter("instance_terminated")
        waiter.wait(
            InstanceIds=[instance_id],
            WaiterConfig={
                "Delay": EC2_WAITER_DELAY_SECONDS,
                "MaxAttempts": EC2_WAITER_MAX_ATTEMPTS,
            },
        )
        logger.info("Instance %s terminated", instance_id)
    except ClientError as e:
        if "InvalidInstanceID.NotFound" not in str(e):
            raise
        logger.warning("Instance %s already terminated", instance_id)
