"""EC2 instance discovery tool — backed by aws_sdk_client.

Enumerates EC2 instances filtered by a ``tier`` tag (or by explicit instance
IDs) so the agent can answer "which application tier(s) plausibly drive load
on this RDS?" without depending on Kubernetes metadata.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.aws.availability import ec2_available_or_backend
from integrations.aws.aws_sdk_client import execute_aws_sdk_call
from integrations.aws.topology_helper import (
    build_ec2_summary,
    extract_ec2_instances_params,
)
from integrations.ec2.tools.ec2_instances_by_tag_tool._evidence import (
    map_ec2_instances_by_tag,
)

logger = logging.getLogger(__name__)


def _is_available(sources: dict[str, dict]) -> bool:
    if not ec2_available_or_backend(sources):
        return False
    ec2 = sources.get("ec2", {})
    return bool(
        ec2.get("tiers") or ec2.get("instance_ids") or ec2.get("vpc_id") or ec2.get("_backend")
    )


_INACTIVE_STATES = frozenset({"terminated", "stopped", "stopping", "shutting-down"})


def _summarize_instance(raw: dict[str, Any]) -> dict[str, Any]:
    tags = {t.get("Key", ""): t.get("Value", "") for t in (raw.get("Tags") or [])}
    placement = raw.get("Placement") or {}
    return {
        "instance_id": raw.get("InstanceId", ""),
        "tier": tags.get("tier") or tags.get("Tier", ""),
        "asg": tags.get("aws:autoscaling:groupName", ""),
        "private_ip": raw.get("PrivateIpAddress", ""),
        "vpc_id": raw.get("VpcId", ""),
        "subnet_id": raw.get("SubnetId", ""),
        "availability_zone": placement.get("AvailabilityZone", ""),
        "state": (raw.get("State") or {}).get("Name", ""),
        "instance_type": raw.get("InstanceType", ""),
        "security_groups": [sg.get("GroupId", "") for sg in (raw.get("SecurityGroups") or [])],
    }


@tool(
    name="ec2_instances_by_tag",
    source="ec2",
    description=(
        "List EC2 instances filtered by ``tier`` tag, instance IDs, or VPC. "
        "Use to enumerate the application tier(s) behind a load balancer or "
        "plausibly driving load on a downstream RDS. Returns a per-tier "
        "grouping so correlation with CloudWatch CPU is straightforward."
    ),
    use_cases=[
        "Discovering EC2 application tiers when investigating a non-K8s alert",
        "Mapping a tier name (web/worker/etc.) to its instance IDs",
        "Bridging EC2 → RDS when answering 'which tier drives DB load'",
    ],
    requires=["region"],
    outputs={
        "instances": "list of EC2 instances with id, tier, asg, vpc, az, state",
        "by_tier": "mapping tier name → instance IDs (the load-attribution view)",
        "tiers_detected": "sorted list of tier values seen across the result set",
        "summary": (
            "agent-friendly precomputed counts: by_tier_counts, total_active, "
            "primary_tier, vpcs_in_scope, azs_in_scope"
        ),
        "truncated": "true if the safety cap stopped pagination before completion",
    },
    input_schema={
        "type": "object",
        "properties": {
            "tier": {"type": "string", "description": "tag value for the 'tier' tag"},
            "instance_ids": {"type": "array", "items": {"type": "string"}, "default": []},
            "vpc_id": {"type": "string", "default": ""},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": [],
    },
    is_available=_is_available,
    extract_params=extract_ec2_instances_params,
    evidence_mapper=map_ec2_instances_by_tag,
)
def ec2_instances_by_tag(
    tier: str = "",
    instance_ids: list[str] | None = None,
    vpc_id: str = "",
    region: str = "us-east-1",
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """List EC2 instances filtered by tag/IDs/VPC.

    When ``aws_backend`` is provided (FixtureAWSBackend in synthetic tests) the
    call short-circuits to the backend. Otherwise calls boto3 ec2 via
    ``execute_aws_sdk_call`` using the default boto3 credential chain.
    """
    instance_ids = instance_ids or []
    logger.info(
        "[ec2] ec2_instances_by_tag tier=%s ids=%d vpc=%s",
        tier or "-",
        len(instance_ids),
        vpc_id or "-",
    )
    if aws_backend is not None:
        return cast(
            "dict[str, Any]",
            aws_backend.describe_instances_by_tag(
                tier=tier,
                instance_ids=instance_ids,
                vpc_id=vpc_id,
            ),
        )

    parameters: dict[str, Any] = {"MaxResults": 1000}
    if instance_ids:
        parameters["InstanceIds"] = instance_ids
        # When a finite ID list is provided, pagination is unnecessary —
        # the response is bounded by len(instance_ids).
        parameters.pop("MaxResults", None)
    filters: list[dict[str, Any]] = []
    if tier:
        filters.append({"Name": "tag:tier", "Values": [tier]})
    if vpc_id:
        filters.append({"Name": "vpc-id", "Values": [vpc_id]})
    if filters:
        parameters["Filters"] = filters

    if not filters and not instance_ids:
        return tool_unavailable("ec2", "tier, instance_ids, or vpc_id is required")

    instances: list[dict[str, Any]] = []
    truncated = False
    next_token: str | None = None
    while True:
        if next_token:
            parameters["NextToken"] = next_token
        result = execute_aws_sdk_call(
            service_name="ec2",
            operation_name="describe_instances",
            parameters=parameters,
            region=region,
        )
        if not result.get("success"):
            return tool_unavailable(
                "ec2", "Failed to describe EC2 instances. Check server logs for details."
            )
        data = result.get("data") or {}
        for reservation in data.get("Reservations") or []:
            for raw in reservation.get("Instances", []) or []:
                summary = _summarize_instance(raw)
                if summary["state"] in _INACTIVE_STATES:
                    # Terminated/stopped instances cannot drive load on a
                    # downstream RDS — exclude them from the topology answer.
                    continue
                instances.append(summary)
        next_token = data.get("NextToken") or None
        if not next_token:
            break
        if len(instances) >= 5000:
            # Safety cap: avoid unbounded loops on misconfigured filters.
            truncated = True
            break

    by_tier: dict[str, list[str]] = {}
    for inst in instances:
        bucket = by_tier.setdefault(inst["tier"] or "untagged", [])
        if inst["instance_id"]:
            bucket.append(inst["instance_id"])

    return {
        "source": "ec2",
        "available": True,
        "tier": tier,
        "vpc_id": vpc_id,
        "total_instances": len(instances),
        "instances": instances,
        "by_tier": by_tier,
        "tiers_detected": sorted(by_tier.keys()),
        "summary": build_ec2_summary(instances, by_tier),
        "truncated": truncated,
        "error": None,
    }
