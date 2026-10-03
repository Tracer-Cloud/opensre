"""Tools: start and stop the signed-in organization's hosted gateway."""

from __future__ import annotations

from typing import Any

from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel
from core.tool_framework import tool
from infrastructure.analytics.capture import (
    capture_hosted_gateway_healthy,
    capture_hosted_gateway_started,
)
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
    gateway_settings_url,
    hosted_gateway_available,
    state_output,
)

START_TOOL_NAME = "start_hosted_gateway"
STOP_TOOL_NAME = "stop_hosted_gateway"

_NO_INPUT = {"type": "object", "properties": {}, "additionalProperties": False}
_WHOSE = (
    "The OpenSRE app finds the gateway from the account the user signed in with; no "
    "organization or gateway id is passed."
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
    is_available=hosted_gateway_available,
    input_schema=_NO_INPUT,
    outputs=STATE_OUTPUTS,
)
def start_hosted_gateway() -> dict[str, Any]:
    """Ask the OpenSRE app to start the signed-in organization's gateway.

    The start is always requested, so the app's refusals apply. A health read
    beforehand only shapes the reply: a gateway that was already running is told
    so. That read is best effort and never blocks the start.
    """
    settings_url = ""
    try:
        with HostedGatewayClient.from_account() as client:
            settings_url = gateway_settings_url(client.app_url)
            was_running = _was_running(client)
            health = client.start()
    except HostedGatewayError as exc:
        return failure_output(
            exc,
            tool_name=START_TOOL_NAME,
            component="integrations.hosted_gateway.tools.gateway_lifecycle.start_hosted_gateway",
            settings_url=settings_url,
        )
    already_running = was_running and health.healthy
    capture_hosted_gateway_started(
        gateway_id=health.gateway_id,
        actual_state=health.actual_state,
        already_running=already_running,
    )
    if health.healthy:
        capture_hosted_gateway_healthy(gateway_id=health.gateway_id, tool_name=START_TOOL_NAME)
    if already_running:
        return state_output(health, _already_running(health))
    return state_output(health, _started(health))


def _was_running(client: HostedGatewayClient) -> bool:
    """Whether the gateway was healthy before the start; unknown reads as not running."""
    try:
        return client.health().healthy
    except HostedGatewayError:
        return False


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
    is_available=hosted_gateway_available,
    input_schema=_NO_INPUT,
    outputs=STATE_OUTPUTS,
)
def stop_hosted_gateway() -> dict[str, Any]:
    """Ask the OpenSRE app to stop the signed-in organization's gateway."""
    settings_url = ""
    try:
        with HostedGatewayClient.from_account() as client:
            settings_url = gateway_settings_url(client.app_url)
            health = client.stop()
    except HostedGatewayError as exc:
        return failure_output(
            exc,
            tool_name=STOP_TOOL_NAME,
            component="integrations.hosted_gateway.tools.gateway_lifecycle.stop_hosted_gateway",
            settings_url=settings_url,
        )
    return state_output(health, _stopped(health))


def _already_running(health: GatewayHealth) -> str:
    name = gateway_name(health)
    return f"Your organization's hosted gateway{name} is already running; nothing to start."


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
