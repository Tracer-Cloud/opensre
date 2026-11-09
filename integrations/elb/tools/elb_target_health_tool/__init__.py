"""ELB target group health tool — backed by aws_sdk_client.

Answers "which targets are healthy / unhealthy behind this load balancer?"
which is the bridge from a public-facing alert (DNS/LB) to the EC2 tier
behind it. Pairs with EC2InstancesByTagTool to enumerate the application
tier(s) plausibly driving load on a downstream RDS.
"""

from __future__ import annotations

import logging
from typing import Any, cast

from core.tool_framework import tool
from core.tool_framework.utils import tool_unavailable
from integrations.aws.availability import ec2_available_or_backend
from integrations.aws.aws_sdk_client import execute_aws_sdk_call
from integrations.aws.topology_helper import (
    build_elb_summary,
    extract_target_health_params,
)
from integrations.elb.tools.elb_target_health_tool._evidence import (
    map_get_elb_target_health,
)

logger = logging.getLogger(__name__)


def _is_available(sources: dict[str, dict]) -> bool:
    if not ec2_available_or_backend(sources):
        return False
    ec2 = sources.get("ec2", {})
    return bool(
        ec2.get("target_group_arns") or ec2.get("load_balancer_arns") or ec2.get("_backend")
    )


@tool(
    name="get_elb_target_health",
    source="ec2",
    description=(
        "Describe ELB v2 target groups and the health of their registered targets. "
        "Use to map a load balancer or target group to the EC2 instance IDs serving "
        "traffic and to identify unhealthy/draining targets."
    ),
    use_cases=[
        "Mapping a target group ARN to the EC2 instances behind it",
        "Identifying unhealthy or draining targets correlated with a request-path alert",
        "Bridging DNS → LB → EC2 when investigating a non-K8s topology",
    ],
    requires=["region"],
    outputs={
        "target_groups": "list of ELB v2 target groups in scope",
        "healthy_targets": "registered targets with state=healthy",
        "unhealthy_targets": "registered targets in any non-healthy state",
        "instance_ids": "deduplicated EC2 instance IDs across all targets",
        "summary": (
            "agent-friendly precomputed counts: total_targets, healthy_count, "
            "unhealthy_count, healthy_ratio_pct, unhealthy_states, target_group_count"
        ),
        "api_errors": (
            "per-target-group failures encountered during describe_target_health; "
            "non-empty means coverage is partial and ``available`` is set to False"
        ),
    },
    input_schema={
        "type": "object",
        "properties": {
            "target_group_arns": {
                "type": "array",
                "items": {"type": "string"},
                "default": [],
                "description": "List of target group ARNs (multi-TG ALBs are common).",
            },
            "target_group_arn": {
                "type": "string",
                "description": "Convenience alias for a single target group ARN.",
            },
            "load_balancer_arn": {"type": "string"},
            "region": {"type": "string", "default": "us-east-1"},
        },
        "required": [],
    },
    is_available=_is_available,
    extract_params=extract_target_health_params,
    evidence_mapper=map_get_elb_target_health,
)
def get_elb_target_health(
    target_group_arns: list[str] | None = None,
    target_group_arn: str = "",
    load_balancer_arn: str = "",
    region: str = "us-east-1",
    aws_backend: Any = None,
    **_kwargs: Any,
) -> dict[str, Any]:
    """Describe ELB target groups and target health.

    When ``aws_backend`` is provided (FixtureAWSBackend in synthetic tests) the
    call short-circuits to the backend. Otherwise calls boto3 elbv2 via
    ``execute_aws_sdk_call`` using the default boto3 credential chain.

    Accepts ``target_group_arns`` (list, canonical) or ``target_group_arn``
    (string, convenience). The two are merged and deduplicated.
    """
    arns = list(target_group_arns or [])
    if target_group_arn and target_group_arn not in arns:
        arns.append(target_group_arn)

    logger.info(
        "[ec2] get_elb_target_health tgs=%d lb=%s",
        len(arns),
        load_balancer_arn or "-",
    )
    if aws_backend is not None:
        return cast(
            "dict[str, Any]",
            aws_backend.describe_target_health(
                target_group_arns=arns,
                load_balancer_arn=load_balancer_arn,
            ),
        )

    if not arns and not load_balancer_arn:
        return tool_unavailable(
            "ec2", "either target_group_arns/target_group_arn or load_balancer_arn is required"
        )

    tg_params: dict[str, Any] = (
        {"TargetGroupArns": arns} if arns else {"LoadBalancerArn": load_balancer_arn}
    )
    groups_result = execute_aws_sdk_call(
        service_name="elbv2",
        operation_name="describe_target_groups",
        parameters=tg_params,
        region=region,
    )
    if not groups_result.get("success"):
        return tool_unavailable(
            "ec2", "Failed to describe target groups. Check server logs for details."
        )
    target_groups = (groups_result.get("data") or {}).get("TargetGroups") or []

    healthy_targets: list[dict[str, Any]] = []
    unhealthy_targets: list[dict[str, Any]] = []
    instance_ids: list[str] = []
    api_errors: list[dict[str, str]] = []
    for tg in target_groups:
        tg_arn = tg.get("TargetGroupArn", "")
        if not tg_arn:
            continue
        health_result = execute_aws_sdk_call(
            service_name="elbv2",
            operation_name="describe_target_health",
            parameters={"TargetGroupArn": tg_arn},
            region=region,
        )
        if not health_result.get("success"):
            # Per-TG failures must be surfaced — silently treating them as
            # "no targets" would let the agent conclude that a tier behind
            # the failing TG is healthy when in fact we have zero coverage.
            api_errors.append(
                {
                    "target_group_arn": tg_arn,
                    "error": str(health_result.get("error") or "unknown"),
                }
            )
            descriptions: list[dict[str, Any]] = []
        else:
            descriptions = (health_result.get("data") or {}).get("TargetHealthDescriptions") or []
        for desc in descriptions:
            target = desc.get("Target", {}) or {}
            health = desc.get("TargetHealth", {}) or {}
            state = health.get("State", "")
            entry = {
                "target_group_arn": tg_arn,
                "instance_id": target.get("Id", ""),
                "port": target.get("Port"),
                "state": state,
                "reason": health.get("Reason", ""),
                "description": health.get("Description", ""),
            }
            if target.get("Id"):
                instance_ids.append(target["Id"])
            if state == "healthy":
                healthy_targets.append(entry)
            else:
                unhealthy_targets.append(entry)

    # When at least one TG queried successfully we still return the partial
    # data — but with `available=False` and a populated `api_errors` list so
    # the agent never silently treats partial coverage as full coverage.
    coverage_complete = not api_errors
    return {
        "source": "ec2",
        "available": coverage_complete,
        "target_groups": target_groups,
        "healthy_targets": healthy_targets,
        "unhealthy_targets": unhealthy_targets,
        "instance_ids": list(dict.fromkeys(instance_ids)),
        "summary": build_elb_summary(target_groups, healthy_targets, unhealthy_targets),
        "api_errors": api_errors,
        "error": (
            None
            if coverage_complete
            else f"Partial coverage: {len(api_errors)}/{len(target_groups)} target groups failed."
        ),
    }
