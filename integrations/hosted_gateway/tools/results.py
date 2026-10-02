"""What the hosted-gateway tools return: the gateway's state, or a plain reason it is unknown."""

from __future__ import annotations

from typing import Any

from config.constants.capabilities import HOSTED_GATEWAY_CAPABILITY
from core.agent_harness.tools import capability_available_from_sources
from core.tool import report_run_error
from integrations.hosted_gateway.client import (
    ERR_GATEWAY_UNAVAILABLE,
    ERR_INSECURE_APP_URL,
    ERR_NOT_PROVISIONED,
    ERR_NOT_RUNNING,
    ERR_NOT_SIGNED_IN,
    ERR_NOT_SUPPORTED,
    ERR_PROMPT_TOO_LARGE,
    ERR_UNAUTHORIZED,
    ERR_UNKNOWN_PROMPT,
    ERR_UNREACHABLE,
    EXPECTED_ERRORS,
    TRANSIENT_ERRORS,
    GatewayHealth,
    HostedGatewayError,
)

SOURCE = "opensre"


def hosted_gateway_available(sources: dict[str, dict[str, Any]]) -> bool:
    return capability_available_from_sources(sources, HOSTED_GATEWAY_CAPABILITY)


_SIGN_IN = "opensre account login"
_FAILURE_TEXT = {
    ERR_NOT_SIGNED_IN: f"You are not signed in to OpenSRE. Run `{_SIGN_IN}` first.",
    ERR_UNAUTHORIZED: f"Your OpenSRE sign-in expired or was revoked. Run `{_SIGN_IN}` again.",
    ERR_NOT_SUPPORTED: "The OpenSRE app you are signed in to does not offer this yet.",
    ERR_NOT_PROVISIONED: "Your organization has no hosted gateway to start or stop yet.",
    ERR_NOT_RUNNING: "Your organization's hosted gateway is not running, so it cannot take a prompt.",
    ERR_UNKNOWN_PROMPT: "The hosted gateway no longer holds that prompt; send it again.",
    ERR_PROMPT_TOO_LARGE: "That prompt is too long for the hosted gateway; shorten it.",
    ERR_UNREACHABLE: (
        "The OpenSRE app did not answer (the connection failed or timed out). Check this "
        "machine's network connection."
    ),
    ERR_GATEWAY_UNAVAILABLE: (
        "Your organization's hosted gateway is not answering right now; it may still be "
        "starting after a restart. Try again in a minute. A restart drops the prompts the "
        "gateway held, so a prompt sent before one has to be sent again."
    ),
    ERR_INSECURE_APP_URL: (
        "The OpenSRE app URL of this sign-in is not https, so the account token was not "
        f"sent. Sign in again with `{_SIGN_IN}`."
    ),
}

#: What the app named, when that is more specific than the status. An empty sentence
#: means the code is known and adds nothing. Anything not listed is reported by name.
_CAUSE_TEXT = {
    "GATEWAY_UNREACHABLE": (
        "The control plane found the gateway's task but could not connect to it (the "
        "connection was refused or timed out). That happens while the gateway is restarting, "
        "including after an integration change in the OpenSRE app, which takes a few minutes. "
        "Try again once it is steady."
    ),
    "too_many_prompts": (
        "The hosted gateway's prompt queue is full, so it refused this prompt. Send it again "
        "in a moment."
    ),
    "prompt_intake_unavailable": (
        "The gateway process is running but is not accepting prompts yet. Try again in a minute."
    ),
    "GATEWAY_CAPACITY_EXCEEDED": (
        "The gateway fleet has reached its limit of active organizations, so this request "
        "was refused."
    ),
    "GATEWAY_CONTRACT_REGRESSION": (
        "Updating the gateway would drop configuration it currently has, so the change was refused."
    ),
    "GATEWAY_STATUS_FAILED": "The control plane could not read the gateway's status.",
    "GATEWAY_RECONCILE_FAILED": "The control plane could not update the gateway's task definition.",
    "GATEWAY_STATE_CHANGE_FAILED": (
        "The control plane could not change whether the gateway is running."
    ),
    "GATEWAY_DELETE_FAILED": "The control plane could not delete the gateway.",
    "GATEWAY_TOKEN_REQUIRED": (
        "The gateway has no listener token, so the control plane refused to call it."
    ),
    "INVALID_LIFECYCLE_INPUT": "The control plane rejected the request as invalid.",
    "invalid_prompt_response": "The gateway answered in a shape the control plane could not read.",
    "gateway_invalid_response": "The gateway's answer was not valid JSON.",
    "internal_error": "The control plane hit an internal error while relaying the request.",
    "gateway_not_configured": "This OpenSRE app is not configured to reach the gateway control plane.",
    "assume_role_failed": (
        "The OpenSRE app could not assume the role it uses to reach the control plane."
    ),
    "gateway_lookup_failed": "The OpenSRE app could not look up the gateway.",
    "gateway_request_failed": "",
    "invalid_json": "The request body was not valid JSON.",
    "invalid_body": "The request body was not a JSON object.",
    "prompt_required": "The prompt was empty.",
    "answer_required": "The answer was empty.",
    "invalid_context": (
        "The prompt's context was not accepted. Each value has to be a short string, and "
        "there is a limit on how many."
    ),
    "invalid_actor": "The actor on the prompt was not accepted.",
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
    "cause_code": "The app's more specific reason, when error_kind is only the status",
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
        "cause_code": "",
        "response_text": response_text,
    }


def failure_output(exc: HostedGatewayError, *, tool_name: str, component: str) -> dict[str, Any]:
    """The tool result for a refused or failed call; only real failures are reported.

    A transient failure is reported as a warning without a stack: the code says it all.
    """
    if exc.code in TRANSIENT_ERRORS:
        report_run_error(
            exc,
            tool_name=tool_name,
            source=SOURCE,
            component=component,
            severity="warning",
            include_traceback=False,
        )
    elif exc.code not in EXPECTED_ERRORS:
        report_run_error(exc, tool_name=tool_name, source=SOURCE, component=component)
    text = _failure_text(exc)
    return {
        "success": False,
        "signed_in": exc.code != ERR_NOT_SIGNED_IN,
        "provisioned": False,
        "healthy": False,
        "error_kind": exc.code,
        "cause_code": exc.cause_code,
        "error": text,
        "response_text": text,
    }


def cause_sentence(exc: HostedGatewayError) -> str:
    """The sentence for ``cause_code``, or "" when it adds nothing beyond ``code``."""
    if not exc.cause_code or exc.cause_code == exc.code:
        return ""
    if exc.cause_code in _CAUSE_TEXT:
        return _CAUSE_TEXT[exc.cause_code]
    return f"The OpenSRE app reported {exc.cause_code}."


def _failure_text(exc: HostedGatewayError) -> str:
    """The status sentence, replaced by a known cause and extended by an unknown one."""
    base = _FAILURE_TEXT.get(exc.code, f"The OpenSRE app could not do that ({exc.code}).")
    cause = cause_sentence(exc)
    if not cause:
        return base
    if exc.cause_code in _CAUSE_TEXT:
        return cause
    return f"{base} {cause}"


def gateway_name(health: GatewayHealth) -> str:
    return f" {health.gateway_id}" if health.gateway_id else ""


__all__ = [
    "HOSTED_GATEWAY_CAPABILITY",
    "SOURCE",
    "STATE_OUTPUTS",
    "cause_sentence",
    "failure_output",
    "gateway_name",
    "hosted_gateway_available",
    "state_output",
]
