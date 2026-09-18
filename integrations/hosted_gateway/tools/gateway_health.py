"""Tool: is the signed-in organization's hosted gateway up?"""

from __future__ import annotations

from typing import Any

from config.constants.hosted_gateway import HOSTED_GATEWAY_SETTINGS_PATH
from core.domain.types.tools import ToolSurface
from core.tool import SideEffectLevel, report_run_error
from core.tool_framework import tool
from integrations.hosted_gateway.client import (
    ERR_INSECURE_APP_URL,
    ERR_NOT_SIGNED_IN,
    ERR_NOT_SUPPORTED,
    ERR_UNAUTHORIZED,
    EXPECTED_ERRORS,
    GatewayHealth,
    HostedGatewayClient,
    HostedGatewayError,
)

TOOL_NAME = "check_hosted_gateway"
SOURCE = "opensre"

_SIGN_IN = "opensre account login"
_FAILURE_TEXT = {
    ERR_NOT_SIGNED_IN: f"You are not signed in to OpenSRE. Run `{_SIGN_IN}` first.",
    ERR_UNAUTHORIZED: f"Your OpenSRE sign-in expired or was revoked. Run `{_SIGN_IN}` again.",
    ERR_NOT_SUPPORTED: (
        "The OpenSRE app you are signed in to does not offer the hosted gateway check yet."
    ),
    ERR_INSECURE_APP_URL: (
        "The OpenSRE app URL of this sign-in is not https, so the account token was not "
        f"sent. Sign in again with `{_SIGN_IN}`."
    ),
}


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
        "Starting, stopping or configuring the hosted gateway",
    ],
    surfaces=(ToolSurface.ACTION, ToolSurface.CHAT),
    side_effect_level=SideEffectLevel.READ_ONLY,
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
    outputs={
        "success": "True when the app answered; the answer may still be 'not running'",
        "signed_in": "False when there is no OpenSRE sign-in on this machine",
        "provisioned": "True when the organization has a hosted gateway",
        "healthy": "True when that gateway is running with nothing pending",
        "gateway_id": "Name of the gateway service, for support requests",
        "desired_state": "running or stopped, as the organization asked",
        "actual_state": "running, provisioning, stopped or failed",
        "last_error_code": "The control plane's last error for this gateway, if any",
        "error_kind": "Stable failure code when success is false",
        "response_text": "One or two sentences for the user",
    },
)
def check_hosted_gateway() -> dict[str, Any]:
    """Look the signed-in account's gateway up through the OpenSRE app and report its state."""
    try:
        with HostedGatewayClient.from_account() as client:
            health = client.health()
            settings_url = f"{client.app_url}{HOSTED_GATEWAY_SETTINGS_PATH}"
    except HostedGatewayError as exc:
        return _failure(exc)
    return {
        "success": True,
        "signed_in": True,
        "provisioned": health.provisioned,
        "healthy": health.healthy,
        "gateway_id": health.gateway_id,
        "desired_state": health.desired_state,
        "actual_state": health.actual_state,
        "last_error_code": health.last_error_code,
        "updated_at": health.updated_at,
        "error_kind": None,
        "response_text": _describe(health, settings_url),
    }


def _describe(health: GatewayHealth, settings_url: str) -> str:
    if not health.provisioned:
        return (
            "Your organization has no hosted gateway yet. An organization admin can set "
            f"it up at {settings_url}."
        )
    name = f" {health.gateway_id}" if health.gateway_id else ""
    if health.healthy:
        return f"Your organization's hosted gateway{name} is running."
    state = health.actual_state or "not running"
    detail = f" Last error: {health.last_error_code}." if health.last_error_code else ""
    if state == "provisioning":
        return f"Your organization's hosted gateway{name} is starting; check again in a minute."
    return f"Your organization's hosted gateway{name} is {state}.{detail} See {settings_url}."


def _failure(exc: HostedGatewayError) -> dict[str, Any]:
    if exc.code not in EXPECTED_ERRORS:
        report_run_error(
            exc,
            tool_name=TOOL_NAME,
            source=SOURCE,
            component="integrations.hosted_gateway.tools.gateway_health.check_hosted_gateway",
        )
    text = _FAILURE_TEXT.get(
        exc.code, f"The OpenSRE app could not answer the gateway health check ({exc.code})."
    )
    return {
        "success": False,
        "signed_in": exc.code != ERR_NOT_SIGNED_IN,
        "provisioned": False,
        "healthy": False,
        "error_kind": exc.code,
        "error": text,
        "response_text": text,
    }


__all__ = ["SOURCE", "TOOL_NAME", "check_hosted_gateway"]
