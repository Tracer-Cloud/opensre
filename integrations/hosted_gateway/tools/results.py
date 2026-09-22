"""What the hosted-gateway tools return: the gateway's state, or a plain reason it is unknown."""

from __future__ import annotations

from typing import Any

from core.agent_harness.tools import capability_available_from_sources
from core.tool import report_run_error
from integrations.hosted_gateway.client import (
    ERR_ADMIN_REQUIRED,
    ERR_INSECURE_APP_URL,
    ERR_NOT_PROVISIONED,
    ERR_NOT_RUNNING,
    ERR_NOT_SIGNED_IN,
    ERR_NOT_SUPPORTED,
    ERR_PROMPT_TOO_LARGE,
    ERR_UNAUTHORIZED,
    ERR_UNKNOWN_PROMPT,
    EXPECTED_ERRORS,
    GatewayHealth,
    HostedGatewayError,
)

SOURCE = "opensre"

#: Withheld where there is no signed-in account to act for: on the hosted gateway itself.
HOSTED_GATEWAY_CAPABILITY = "hosted_gateway"


def hosted_gateway_available(sources: dict[str, dict[str, Any]]) -> bool:
    return capability_available_from_sources(sources, HOSTED_GATEWAY_CAPABILITY)


_SIGN_IN = "opensre account login"
_FAILURE_TEXT = {
    ERR_NOT_SIGNED_IN: f"You are not signed in to OpenSRE. Run `{_SIGN_IN}` first.",
    ERR_UNAUTHORIZED: f"Your OpenSRE sign-in expired or was revoked. Run `{_SIGN_IN}` again.",
    ERR_NOT_SUPPORTED: "The OpenSRE app you are signed in to does not offer this yet.",
    ERR_ADMIN_REQUIRED: "Only an organization admin can start or stop the hosted gateway.",
    ERR_NOT_PROVISIONED: "Your organization has no hosted gateway to start or stop yet.",
    ERR_NOT_RUNNING: "Your organization's hosted gateway is not running, so it cannot take a prompt.",
    ERR_UNKNOWN_PROMPT: "The hosted gateway no longer holds that prompt; send it again.",
    ERR_PROMPT_TOO_LARGE: "That prompt is too long for the hosted gateway; shorten it.",
    ERR_INSECURE_APP_URL: (
        "The OpenSRE app URL of this sign-in is not https, so the account token was not "
        f"sent. Sign in again with `{_SIGN_IN}`."
    ),
}

STATE_OUTPUTS = {
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
}


def state_output(health: GatewayHealth, response_text: str) -> dict[str, Any]:
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
        "response_text": response_text,
    }


def failure_output(exc: HostedGatewayError, *, tool_name: str, component: str) -> dict[str, Any]:
    """The tool result for a refused or failed call; only real failures are reported."""
    if exc.code not in EXPECTED_ERRORS:
        report_run_error(exc, tool_name=tool_name, source=SOURCE, component=component)
    text = _FAILURE_TEXT.get(exc.code, f"The OpenSRE app could not do that ({exc.code}).")
    return {
        "success": False,
        "signed_in": exc.code != ERR_NOT_SIGNED_IN,
        "provisioned": False,
        "healthy": False,
        "error_kind": exc.code,
        "error": text,
        "response_text": text,
    }


def gateway_name(health: GatewayHealth) -> str:
    return f" {health.gateway_id}" if health.gateway_id else ""


__all__ = [
    "HOSTED_GATEWAY_CAPABILITY",
    "SOURCE",
    "STATE_OUTPUTS",
    "failure_output",
    "gateway_name",
    "hosted_gateway_available",
    "state_output",
]
