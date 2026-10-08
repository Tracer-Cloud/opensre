"""Interactive setup specification for the MCP gateway."""

from __future__ import annotations

from config.constants.mcp_gateway import (
    MCP_GATEWAY_ALLOWED_TOOLS_ENV,
    MCP_GATEWAY_AUTH_TOKEN_ENV,
    MCP_GATEWAY_READ_ONLY_TOOLS_ENV,
    MCP_GATEWAY_URL_ENV,
)
from integrations.mcp_gateway.verifier import verify_mcp_gateway
from integrations.setup_flow import IntegrationSetupSpec, SetupField

MCP_GATEWAY_SETUP = IntegrationSetupSpec(
    service="mcp_gateway",
    fields=(
        SetupField(
            name="url",
            label="MCP gateway URL",
            prompt="Streamable HTTP MCP URL",
            env_var=MCP_GATEWAY_URL_ENV,
        ),
        SetupField(
            name="auth_token",
            label="MCP gateway auth token",
            prompt="Bearer token (optional)",
            env_var=MCP_GATEWAY_AUTH_TOKEN_ENV,
            required=False,
            secret=True,
        ),
        SetupField(
            name="allowed_tools",
            label="Allowed MCP tools",
            prompt="Allowed tool names, comma-separated (optional; blank allows all)",
            env_var=MCP_GATEWAY_ALLOWED_TOOLS_ENV,
            required=False,
        ),
        SetupField(
            name="read_only_tools",
            label="Read-only MCP tools",
            prompt="Exact tool names certified read-only, comma-separated (optional)",
            env_var=MCP_GATEWAY_READ_ONLY_TOOLS_ENV,
            required=False,
        ),
    ),
    verify=verify_mcp_gateway,
)

__all__ = ["MCP_GATEWAY_SETUP"]
