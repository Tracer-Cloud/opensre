"""Tools: start and stop the signed-in organization's hosted gateway."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from integrations.hosted_gateway.client import (
    GatewayHealth,
    HostedGatewayClient,
    HostedGatewayError,
)
from integrations.hosted_gateway.tools.results import (
    SOURCE,
    STATE_OUTPUTS,
    failure_output,
    gateway_name,
    state_output,
)

START_TOOL_NAME = "start_hosted_gateway"
STOP_TOOL_NAME = "stop_hosted_gateway"

_NO_INPUT = {"type": "object", "properties": {}, "additionalProperties": False}
_WHOSE = (
    "The OpenSRE app finds the gateway from the account the user signed in with and allows "
    "this to organization admins only; no organization or gateway id is passed."
)


@tool(
    name=START_TOOL_NAME,
    source=SOURCE,
    display_name="Start hosted gateway",
    description=(
        "Start the OpenSRE hosted gateway of the signed-in user's organization (the managed "
        "Fargate container that runs CI/CD repair loops remotely). It resumes with the "
        f"configuration and credentials it had before. {_WHOSE}"
    ),
    use_cases=["Start the organization's hosted gateway after it was stopped"],
    anti_examples=[
        "Starting the local gateway daemon on this machine (use /gateway start)",
        "Creating a hosted gateway for an organization that has none",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    requires_approval=True,
    approval_reason=(
        "Starts the organization's hosted gateway on Fargate; it runs, and is billed, until "
        "it is stopped."
    ),
    input_schema=_NO_INPUT,
    outputs=STATE_OUTPUTS,
)
def start_hosted_gateway() -> dict[str, Any]:
    """Ask the OpenSRE app to start the signed-in organization's gateway."""
    try:
        with HostedGatewayClient.from_account() as client:
            health = client.start()
    except HostedGatewayError as exc:
        return failure_output(
            exc,
            tool_name=START_TOOL_NAME,
            component="integrations.hosted_gateway.tools.gateway_lifecycle.start_hosted_gateway",
        )
    return state_output(health, _started(health))


@tool(
    name=STOP_TOOL_NAME,
    source=SOURCE,
    display_name="Stop hosted gateway",
    description=(
        "Stop the OpenSRE hosted gateway of the signed-in user's organization. Its "
        "configuration and credentials are kept, so it can be started again; while it is "
        f"stopped, the loops and chat integrations it serves do not run. {_WHOSE}"
    ),
    use_cases=["Stop the organization's hosted gateway, for example before rolling a new image"],
    anti_examples=[
        "Stopping the local gateway daemon on this machine (use /gateway stop)",
        "Deleting the hosted gateway or its data",
    ],
    surfaces=(ToolSurface.ACTION,),
    side_effect_level=SideEffectLevel.MUTATING,
    requires_approval=True,
    approval_reason=(
        "Stops the organization's hosted gateway: the loops and chat integrations it serves "
        "for the whole organization stop until it is started again."
    ),
    input_schema=_NO_INPUT,
    outputs=STATE_OUTPUTS,
)
def stop_hosted_gateway() -> dict[str, Any]:
    """Ask the OpenSRE app to stop the signed-in organization's gateway."""
    try:
        with HostedGatewayClient.from_account() as client:
            health = client.stop()
    except HostedGatewayError as exc:
        return failure_output(
            exc,
            tool_name=STOP_TOOL_NAME,
            component="integrations.hosted_gateway.tools.gateway_lifecycle.stop_hosted_gateway",
        )
    return state_output(health, _stopped(health))


def _started(health: GatewayHealth) -> str:
    name = gateway_name(health)
    if health.healthy:
        return f"Your organization's hosted gateway{name} is running."
    return (
        f"Asked your organization's hosted gateway{name} to start; it is "
        f"{health.actual_state or 'starting'} now. Check it again in a minute."
    )


def _stopped(health: GatewayHealth) -> str:
    name = gateway_name(health)
    if health.actual_state == "stopped":
        return (
            f"Your organization's hosted gateway{name} is stopped. Its configuration is "
            "kept; start it again when you need it."
        )
    return (
        f"Asked your organization's hosted gateway{name} to stop; it is "
        f"{health.actual_state or 'stopping'} now. Its configuration is kept."
    )


__all__ = ["START_TOOL_NAME", "STOP_TOOL_NAME", "start_hosted_gateway", "stop_hosted_gateway"]
