"""VPC lookup and security group management."""

import logging
from typing import Any

from botocore.exceptions import ClientError

from tests.shared.infrastructure_sdk.deployer import (
    DEFAULT_REGION,
    get_boto3_client,
    get_standard_tags,
)

logger = logging.getLogger(__name__)


def get_default_vpc(region: str = DEFAULT_REGION) -> dict[str, Any]:
    """Get default VPC.

    Args:
        region: AWS region.

    Returns:
        Dictionary with VPC info: vpc_id, cidr.

    Raises:
        ValueError: If no default VPC exists.
    """
    ec2_client = get_boto3_client("ec2", region)

    response = ec2_client.describe_vpcs(
        Filters=[{"Name": "is-default", "Values": ["true"]}]
    )

    if not response["Vpcs"]:
        raise ValueError(f"No default VPC found in region {region}")

    vpc = response["Vpcs"][0]
    return {
        "vpc_id": vpc["VpcId"],
        "cidr": vpc["CidrBlock"],
    }


def get_public_subnets(vpc_id: str, region: str = DEFAULT_REGION) -> list[str]:
    """Get public subnet IDs in VPC.

    Args:
        vpc_id: VPC ID.
        region: AWS region.

    Returns:
        List of public subnet IDs.
    """
    ec2_client = get_boto3_client("ec2", region)

    # Get all subnets in VPC
    response = ec2_client.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )

    # For default VPC, all subnets are public
    # For custom VPCs, check route tables for internet gateway
    subnet_ids = []

    for subnet in response["Subnets"]:
        # In default VPC, MapPublicIpOnLaunch indicates public subnet
        if subnet.get("MapPublicIpOnLaunch", False):
            subnet_ids.append(subnet["SubnetId"])
        else:
            # Check if subnet has route to internet gateway
            if _has_internet_gateway_route(subnet["SubnetId"], ec2_client):
                subnet_ids.append(subnet["SubnetId"])

    # If no public subnets found, return all subnets (default VPC case)
    if not subnet_ids:
        subnet_ids = [s["SubnetId"] for s in response["Subnets"]]

    return subnet_ids


def get_private_subnets(vpc_id: str, region: str = DEFAULT_REGION) -> list[str]:
    """Get private subnet IDs in VPC.

    Args:
        vpc_id: VPC ID.
        region: AWS region.

    Returns:
        List of private subnet IDs.
    """
    ec2_client = get_boto3_client("ec2", region)

    response = ec2_client.describe_subnets(
        Filters=[{"Name": "vpc-id", "Values": [vpc_id]}]
    )

    public_subnets = set(get_public_subnets(vpc_id, region))

    return [
        s["SubnetId"]
        for s in response["Subnets"]
        if s["SubnetId"] not in public_subnets
    ]


def _has_internet_gateway_route(subnet_id: str, ec2_client: Any) -> bool:
    """Check if subnet has route to internet gateway."""
    # Get route table for subnet
    response = ec2_client.describe_route_tables(
        Filters=[{"Name": "association.subnet-id", "Values": [subnet_id]}]
    )

    if not response["RouteTables"]:
        # Check main route table
        response = ec2_client.describe_route_tables(
            Filters=[{"Name": "association.main", "Values": ["true"]}]
        )

    for rt in response.get("RouteTables", []):
        for route in rt.get("Routes", []):
            if route.get("GatewayId", "").startswith("igw-"):
                return True

    return False


def create_security_group(
    name: str,
    vpc_id: str,
    description: str,
    ingress_rules: list[dict[str, Any]] | None = None,
    stack_name: str | None = None,
    region: str = DEFAULT_REGION,
) -> dict[str, Any]:
    """Create security group.

    Args:
        name: Security group name.
        vpc_id: VPC ID.
        description: Security group description.
        ingress_rules: List of ingress rules: [{"port": 80, "cidr": "0.0.0.0/0"}].
        stack_name: Stack name for tagging.
        region: AWS region.

    Returns:
        Dictionary with security group info: group_id, arn.
    """
    ec2_client = get_boto3_client("ec2", region)

    # Check if exists
    try:
        response = ec2_client.describe_security_groups(
            Filters=[
                {"Name": "group-name", "Values": [name]},
                {"Name": "vpc-id", "Values": [vpc_id]},
            ]
        )
        if response["SecurityGroups"]:
            sg = response["SecurityGroups"][0]
            return {
                "group_id": sg["GroupId"],
                "arn": f"arn:aws:ec2:{region}:{sg['OwnerId']}:security-group/{sg['GroupId']}",
            }
    except ClientError:
        # Security group doesn't exist yet; fall through to create
        logger.debug("Security group lookup failed before create", exc_info=True)

    # Create new
    tag_specs = []
    if stack_name:
        tag_specs = [
            {
                "ResourceType": "security-group",
                "Tags": get_standard_tags(stack_name) + [{"Key": "Name", "Value": name}],
            }
        ]

    response = ec2_client.create_security_group(
        GroupName=name,
        Description=description,
        VpcId=vpc_id,
        TagSpecifications=tag_specs if tag_specs else None,
    )

    group_id = response["GroupId"]

    # Add ingress rules
    if ingress_rules:
        for rule in ingress_rules:
            _add_ingress_rule(ec2_client, group_id, rule)

    # Get owner ID for ARN
    sg_response = ec2_client.describe_security_groups(GroupIds=[group_id])
    owner_id = sg_response["SecurityGroups"][0]["OwnerId"]

    return {
        "group_id": group_id,
        "arn": f"arn:aws:ec2:{region}:{owner_id}:security-group/{group_id}",
    }


def _add_ingress_rule(ec2_client: Any, group_id: str, rule: dict[str, Any]) -> None:
    """Add an ingress rule to security group."""
    port = rule.get("port")
    cidr = rule.get("cidr", "0.0.0.0/0")
    protocol = rule.get("protocol", "tcp")
    from_port = rule.get("from_port", port)
    to_port = rule.get("to_port", port)
    description = rule.get("description", f"Allow port {port}")

    ip_permission: dict[str, Any] = {
        "IpProtocol": protocol,
        "FromPort": from_port,
        "ToPort": to_port,
    }

    if cidr:
        ip_permission["IpRanges"] = [{"CidrIp": cidr, "Description": description}]

    if rule.get("source_security_group"):
        ip_permission["UserIdGroupPairs"] = [{"GroupId": rule["source_security_group"]}]

    try:
        ec2_client.authorize_security_group_ingress(
            GroupId=group_id,
            IpPermissions=[ip_permission],
        )
    except ClientError as e:
        if e.response["Error"]["Code"] != "InvalidPermission.Duplicate":
            raise


def add_ingress_rule(
    group_id: str,
    port: int,
    cidr: str = "0.0.0.0/0",
    protocol: str = "tcp",
    description: str | None = None,
    region: str = DEFAULT_REGION,
) -> None:
    """Add ingress rule to existing security group.

    Args:
        group_id: Security group ID.
        port: Port number.
        cidr: CIDR block for source.
        protocol: Protocol (tcp, udp, icmp).
        description: Rule description.
        region: AWS region.
    """
    ec2_client = get_boto3_client("ec2", region)

    rule = {
        "port": port,
        "cidr": cidr,
        "protocol": protocol,
        "description": description or f"Allow port {port}",
    }

    _add_ingress_rule(ec2_client, group_id, rule)


def delete_security_group(group_id: str, region: str = DEFAULT_REGION) -> None:
    """Delete security group.

    Args:
        group_id: Security group ID.
        region: AWS region.
    """
    ec2_client = get_boto3_client("ec2", region)

    try:
        ec2_client.delete_security_group(GroupId=group_id)
    except ClientError as e:
        if e.response["Error"]["Code"] not in ["InvalidGroup.NotFound"]:
            raise


def get_security_group(group_id: str, region: str = DEFAULT_REGION) -> dict[str, Any] | None:
    """Get security group details.

    Args:
        group_id: Security group ID.
        region: AWS region.

    Returns:
        Security group details or None if not found.
    """
    ec2_client = get_boto3_client("ec2", region)

    try:
        response = ec2_client.describe_security_groups(GroupIds=[group_id])
        if response["SecurityGroups"]:
            sg = response["SecurityGroups"][0]
            return {
                "group_id": sg["GroupId"],
                "group_name": sg["GroupName"],
                "vpc_id": sg["VpcId"],
                "description": sg["Description"],
            }
        return None
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidGroup.NotFound":
            return None
        raise


def get_availability_zones(region: str = DEFAULT_REGION) -> list[str]:
    """Get available availability zones in region.

    Args:
        region: AWS region.

    Returns:
        List of availability zone names.
    """
    ec2_client = get_boto3_client("ec2", region)

    response = ec2_client.describe_availability_zones(
        Filters=[{"Name": "state", "Values": ["available"]}]
    )

    return [az["ZoneName"] for az in response["AvailabilityZones"]]
