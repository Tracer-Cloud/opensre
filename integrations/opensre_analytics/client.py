"""MCP calls against the OpenSRE Analytics app, with setup-aware error text."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from http import HTTPStatus
from typing import Any

import httpx

from integrations._validation_helpers import report_validation_failure
from integrations.mcp_client import (
    McpSessionOptions,
    call_mcp_tool,
    list_mcp_tools,
    root_cause_message,
)
from integrations.opensre_analytics.config import (
    DEFAULT_OPENSRE_ANALYTICS_URL,
    SERVICE,
    OpenSREAnalyticsConfig,
)

logger = logging.getLogger(__name__)


class AnalyticsToolError(RuntimeError):
    """The analytics app answered a tool call with an error (e.g. a source outage)."""


@dataclass(frozen=True)
class AnalyticsValidationResult:
    """Result of validating an OpenSRE Analytics connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def _session_options(config: OpenSREAnalyticsConfig) -> McpSessionOptions:
    return {
        "session_url": config.url,
        "stdio_env": {},
        "integration_name": "OpenSRE Analytics",
        "config_env_name": "OPENSRE_ANALYTICS",
        "streamable_url_hint": DEFAULT_OPENSRE_ANALYTICS_URL,
    }


def unavailable_reason(config: OpenSREAnalyticsConfig) -> str | None:
    """Setup error when the config cannot be used, else None."""
    if not config.auth_token:
        return (
            "OpenSRE Analytics is not configured: connect it from the OpenSRE dashboard "
            "or set OPENSRE_ANALYTICS_API_TOKEN to the analytics app's API token."
        )
    return None


def describe_error(err: BaseException, config: OpenSREAnalyticsConfig) -> str:
    """Human-readable error with a setup hint; never includes the token."""
    detail = root_cause_message(err, timeout_message="OpenSRE Analytics call timed out")
    if isinstance(err, httpx.HTTPStatusError):
        status = err.response.status_code
        if status == HTTPStatus.UNAUTHORIZED:
            return "OpenSRE Analytics rejected the API token. Reconnect it with the current token."
        if status == HTTPStatus.SERVICE_UNAVAILABLE:
            return "OpenSRE Analytics has no API token configured on the server yet."
    if isinstance(err, (httpx.ConnectError, httpx.ConnectTimeout)):
        return f"Could not reach OpenSRE Analytics at {config.url}."
    if "timed out" in detail.lower():
        return (
            f"OpenSRE Analytics did not answer within {config.timeout_seconds:.0f}s; "
            "cold reports can take tens of seconds, retry once."
        )
    return detail


def list_tools(config: OpenSREAnalyticsConfig) -> list[dict[str, object]]:
    """List the analytics app's MCP tools."""
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": tool.input_schema,
        }
        for tool in list_mcp_tools(
            config,
            timeout_entire_operation=True,
            **_session_options(config),
        )
    ]


def call_tool(
    config: OpenSREAnalyticsConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> dict[str, object]:
    """Call one analytics MCP tool and return the normalized MCP result."""
    return call_mcp_tool(
        config,
        tool_name,
        arguments,
        timeout_call=False,
        timeout_entire_operation=True,
        **_session_options(config),
    )


def tool_payload(result: dict[str, object]) -> Any:
    """The tool's JSON result: ``structuredContent`` when sent, else the parsed text."""
    structured = result.get("structured_content")
    if structured is not None:
        return structured
    text = str(result.get("text") or "")
    try:
        return json.loads(text)
    except ValueError:
        return text


def call_tool_json(
    config: OpenSREAnalyticsConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> dict[str, Any]:
    """Call a tool whose result is a JSON object; raise on a tool error or other shape.

    The analytics app reports a source outage as a tool error rather than an
    empty result, so callers never read an outage as zero activity.
    """
    result = call_tool(config, tool_name, arguments)
    if result.get("is_error"):
        message = str(result.get("text") or "").strip() or f"{tool_name} failed."
        raise AnalyticsToolError(message)
    payload = tool_payload(result)
    if not isinstance(payload, dict):
        raise AnalyticsToolError(f"{tool_name} returned an unexpected result.")
    if payload.get("available") is False:
        raise AnalyticsToolError(str(payload.get("error") or f"{tool_name} is unavailable."))
    return payload


def validate_config(config: OpenSREAnalyticsConfig) -> AnalyticsValidationResult:
    """Validate connectivity and the token by listing the app's tools."""
    reason = unavailable_reason(config)
    if reason is not None:
        return AnalyticsValidationResult(ok=False, detail=reason)
    try:
        names = tuple(sorted(str(tool["name"]) for tool in list_tools(config)))
    except Exception as err:
        report_validation_failure(err, logger=logger, integration=SERVICE, method="validate_config")
        return AnalyticsValidationResult(
            ok=False, detail=f"OpenSRE Analytics validation failed: {describe_error(err, config)}"
        )
    if not names:
        return AnalyticsValidationResult(
            ok=False, detail=f"OpenSRE Analytics at {config.url} exposed no tools."
        )
    return AnalyticsValidationResult(
        ok=True,
        detail=f"Connected to OpenSRE Analytics ({config.url}); {len(names)} tools available.",
        tool_names=names,
    )


__all__ = [
    "AnalyticsToolError",
    "AnalyticsValidationResult",
    "call_tool",
    "call_tool_json",
    "describe_error",
    "list_tools",
    "tool_payload",
    "unavailable_reason",
    "validate_config",
]
