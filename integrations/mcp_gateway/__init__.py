"""Public API for the generic MCP gateway integration."""

from integrations.mcp_gateway.classifier import classify
from integrations.mcp_gateway.client import McpGatewayClient, McpGatewayToolDescriptor
from integrations.mcp_gateway.config import McpGatewayConfig, build_mcp_gateway_config
from integrations.mcp_gateway.errors import (
    McpGatewayError,
    McpGatewayRefused,
    McpGatewayRequestError,
    describe_mcp_gateway_error,
)
from integrations.mcp_gateway.verifier import (
    McpGatewayValidationResult,
    validate_mcp_gateway_config,
)

__all__ = [
    "McpGatewayClient",
    "McpGatewayConfig",
    "McpGatewayError",
    "McpGatewayRefused",
    "McpGatewayRequestError",
    "McpGatewayToolDescriptor",
    "McpGatewayValidationResult",
    "build_mcp_gateway_config",
    "classify",
    "describe_mcp_gateway_error",
    "validate_mcp_gateway_config",
]
