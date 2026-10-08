"""Secret-safe error classification for the MCP gateway."""

from __future__ import annotations

import re
from http import HTTPStatus

import httpx

from config.constants.mcp_gateway import (
    MCP_GATEWAY_AUTH_TOKEN_ENV,
    MCP_GATEWAY_DEFAULT_TIMEOUT_SECONDS,
    MCP_GATEWAY_URL_ENV,
)
from integrations.mcp_client import McpResponseTooLargeError, McpToolCallOutcomeUnknownError

_BEARER_SECRET = re.compile(r"(?i)bearer\s+[^\s,;]+")


class McpGatewayError(RuntimeError):
    """Base class for safe, operator-facing gateway failures."""


class McpGatewayRequestError(McpGatewayError):
    """A transport or protocol failure with all sensitive detail removed."""


class McpGatewayRefused(McpGatewayError):
    """A local policy refusal that did not invoke the requested MCP tool."""


def _exceptions(exc: BaseException) -> list[BaseException]:
    pending = [exc]
    result: list[BaseException] = []
    seen: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        result.append(current)
        if isinstance(current, BaseExceptionGroup):
            pending.extend(current.exceptions)
        if isinstance(current.__cause__, BaseException):
            pending.append(current.__cause__)
        if isinstance(current.__context__, BaseException):
            pending.append(current.__context__)
    return result


def sanitize_mcp_gateway_text(text: str, *, auth_token: str) -> str:
    """Remove configured and bearer-shaped secrets from diagnostic text."""
    sanitized = text.replace(auth_token, "[REDACTED]") if auth_token else text
    return _BEARER_SECRET.sub("Bearer [REDACTED]", sanitized)


def describe_mcp_gateway_error(
    exc: BaseException,
    *,
    auth_token: str,
    timeout_seconds: float = MCP_GATEWAY_DEFAULT_TIMEOUT_SECONDS,
    mutation_outcome_unknown: bool = False,
) -> str:
    """Return a stable, secret-safe explanation for a gateway failure."""
    if isinstance(exc, McpGatewayError):
        return sanitize_mcp_gateway_text(str(exc), auth_token=auth_token)

    nested = _exceptions(exc)
    call_outcome_unknown = mutation_outcome_unknown and any(
        isinstance(item, McpToolCallOutcomeUnknownError) for item in nested
    )
    if call_outcome_unknown:
        if any(isinstance(item, McpResponseTooLargeError) for item in nested):
            reason = "its response exceeded the safety limit"
        else:
            reason = "its response was not received"
        return (
            f"The MCP gateway tool may have completed, but {reason}, so the outcome is "
            "unknown. Do not retry a mutation automatically; verify the remote system first."
        )
    status_error = next(
        (item for item in nested if isinstance(item, httpx.HTTPStatusError)),
        None,
    )
    if status_error is not None:
        status = status_error.response.status_code
        if status in (HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN):
            return (
                "MCP gateway authentication failed. Check "
                f"{MCP_GATEWAY_AUTH_TOKEN_ENV} and the server's token policy."
            )
        if status == HTTPStatus.NOT_FOUND:
            return (
                f"MCP gateway endpoint was not found. Check {MCP_GATEWAY_URL_ENV}; "
                "Streamable HTTP servers commonly use the /mcp path."
            )
        return f"MCP gateway returned HTTP {status}."

    if any(str(item).strip().lower() == "not found" for item in nested):
        return (
            f"MCP gateway endpoint was not found. Check {MCP_GATEWAY_URL_ENV}; "
            "Streamable HTTP servers commonly use the /mcp path."
        )

    if any(isinstance(item, (httpx.ConnectError, httpx.ConnectTimeout)) for item in nested):
        return f"Could not reach the MCP gateway. Check {MCP_GATEWAY_URL_ENV} and network access."
    if any(isinstance(item, TimeoutError) for item in nested):
        return f"MCP gateway operation timed out after {timeout_seconds:g} seconds."
    if any("server returned an error response" in str(item).lower() for item in nested):
        return (
            "The MCP gateway rejected the connection. Check "
            f"{MCP_GATEWAY_AUTH_TOKEN_ENV} and the server's authentication settings."
        )
    return f"MCP gateway request failed: {type(exc).__name__}."


def safe_request_error(
    exc: BaseException,
    *,
    auth_token: str,
    timeout_seconds: float = MCP_GATEWAY_DEFAULT_TIMEOUT_SECONDS,
    mutation_outcome_unknown: bool = False,
) -> McpGatewayRequestError:
    """Create a detached safe exception suitable for logs and tool output."""
    return McpGatewayRequestError(
        describe_mcp_gateway_error(
            exc,
            auth_token=auth_token,
            timeout_seconds=timeout_seconds,
            mutation_outcome_unknown=mutation_outcome_unknown,
        )
    )
