"""Connectivity and policy verification for the MCP gateway."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from integrations._validation_helpers import report_validation_failure
from integrations.mcp_gateway.client import McpGatewayClient
from integrations.mcp_gateway.config import McpGatewayConfig, build_mcp_gateway_config
from integrations.mcp_gateway.redaction import public_tool_name
from integrations.verification import register_validation_verifier

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class McpGatewayValidationResult:
    """Result of validating MCP gateway connectivity and configured names."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def validate_mcp_gateway_config(config: McpGatewayConfig) -> McpGatewayValidationResult:
    """List tools and verify that every configured policy name exists."""
    try:
        tools = McpGatewayClient(config).list_all_tools()
    except Exception as exc:
        report_validation_failure(
            exc,
            logger=logger,
            integration="mcp_gateway",
            method="validate_mcp_gateway_config",
        )
        return McpGatewayValidationResult(ok=False, detail=str(exc))

    advertised = {tool["name"] for tool in tools}
    if not advertised:
        return McpGatewayValidationResult(
            ok=False,
            detail="MCP gateway connected but exposed no tools.",
        )

    configured = set(config.allowed_tools) | set(config.read_only_tools)
    missing = sorted(configured.difference(advertised))
    if missing:
        return McpGatewayValidationResult(
            ok=False,
            detail="MCP gateway did not advertise configured tool(s): "
            + ", ".join(public_tool_name(name, config.auth_token) for name in missing),
        )

    effective = (
        advertised if not config.allowed_tools else advertised.intersection(config.allowed_tools)
    )
    read_only_count = len(effective.intersection(config.read_only_tools))
    approval_count = len(effective) - read_only_count
    return McpGatewayValidationResult(
        ok=True,
        detail=(
            f"MCP gateway connected; discovered {len(effective)} tool(s): "
            f"{read_only_count} read-only and {approval_count} approval-required."
        ),
        tool_names=tuple(sorted(public_tool_name(name, config.auth_token) for name in effective)),
    )


verify_mcp_gateway = register_validation_verifier(
    "mcp_gateway",
    build_config=build_mcp_gateway_config,
    validate_config=validate_mcp_gateway_config,
)
