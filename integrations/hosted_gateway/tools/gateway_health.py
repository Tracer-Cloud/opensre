"""Tool: is the signed-in organization's hosted gateway up?"""

from __future__ import annotations

from typing import Any

from config.constants.hosted_gateway import HOSTED_GATEWAY_SETTINGS_PATH
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

TOOL_NAME = "check_hosted_gateway"
_COMPONENT = "integrations.hosted_gateway.tools.gateway_health.check_hosted_gateway"


@tool(
    name=TOOL_NAME,
    source=SOURCE,
    display_name="Check hosted gateway",
    description=(
        "Check whether the signed-in user's organization has an OpenSRE hosted gateway "
        "(the managed Fargate container that runs CI/CD repair loops remotely) and "
        "whether it is running. The OpenSRE app finds the gateway from the account the "
        "user signed in with; no organization or gateway id is passed. Read-only."
    ),
    use_cases=[
        "Check that the hosted gateway of the user's organization is reachable and running",
        "Find out whether the organization has a hosted gateway before delegating work to it",
    ],
    anti_examples=[
        "Health of the local gateway daemon on this machine (use /gateway status)",
        "Starting or stopping the hosted gateway (use start_hosted_gateway, stop_hosted_gateway)",
    ],
    surfaces=(ToolSurface.ACTION, ToolSurface.CHAT),
    side_effect_level=SideEffectLevel.READ_ONLY,
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    outputs=STATE_OUTPUTS,
)
def check_hosted_gateway() -> dict[str, Any]:
    """Look the signed-in account's gateway up through the OpenSRE app and report its state."""
    try:
        with HostedGatewayClient.from_account() as client:
            health = client.health()
            settings_url = f"{client.app_url}{HOSTED_GATEWAY_SETTINGS_PATH}"
    except HostedGatewayError as exc:
        return failure_output(exc, tool_name=TOOL_NAME, component=_COMPONENT)
    return state_output(health, _describe(health, settings_url))


def _describe(health: GatewayHealth, settings_url: str) -> str:
    if not health.provisioned:
        return (
            "Your organization has no hosted gateway yet. An organization admin can set "
            f"it up at {settings_url}."
        )
    name = gateway_name(health)
    if health.healthy:
        return f"Your organization's hosted gateway{name} is running."
    state = health.actual_state or "not running"
    detail = f" Last error: {health.last_error_code}." if health.last_error_code else ""
    if state == "provisioning":
        return f"Your organization's hosted gateway{name} is starting; check again in a minute."
    return f"Your organization's hosted gateway{name} is {state}.{detail} See {settings_url}."


__all__ = ["SOURCE", "TOOL_NAME", "check_hosted_gateway"]
