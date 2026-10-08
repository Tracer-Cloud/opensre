"""Least-privilege client for a configured MCP gateway."""

from __future__ import annotations

from typing import TypedDict

from config.constants.mcp_gateway import MCP_GATEWAY_TOOL_RESPONSE_MAX_BYTES
from integrations.mcp_client import McpSessionOptions, call_mcp_tool, list_mcp_tools
from integrations.mcp_gateway.config import McpGatewayConfig
from integrations.mcp_gateway.errors import McpGatewayRefused, safe_request_error
from integrations.mcp_gateway.redaction import public_tool_name
from integrations.mcp_gateway.results import safe_tool_result


class McpGatewayToolDescriptor(TypedDict):
    """A tool advertised by the configured MCP server."""

    name: str
    description: str
    input_schema: object | None


class McpGatewayClient:
    """List and call tools while enforcing the configured exact-name allowlist."""

    def __init__(self, config: McpGatewayConfig) -> None:
        self.config = config

    def _session_options(self) -> McpSessionOptions:
        return {
            "session_url": self.config.url,
            "stdio_env": {},
            "integration_name": "MCP gateway",
            "config_env_name": "MCP_GATEWAY",
            "streamable_url_hint": "http://127.0.0.1:8765/mcp",
        }

    def list_all_tools(self) -> list[McpGatewayToolDescriptor]:
        """Return original descriptors for policy checks; outputs must redact copies."""
        try:
            tools = list_mcp_tools(
                self.config,
                timeout_entire_operation=True,
                **self._session_options(),
            )
        except Exception as exc:
            error = safe_request_error(
                exc,
                auth_token=self.config.auth_token,
                timeout_seconds=self.config.timeout_seconds,
            )
        else:
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.input_schema,
                }
                for tool in tools
            ]
        raise error

    def list_tools(self) -> list[McpGatewayToolDescriptor]:
        """List server tools after applying the optional overall allowlist."""
        tools = self.list_all_tools()
        if not self.config.allowed_tools:
            return tools
        allowed = set(self.config.allowed_tools)
        return [tool for tool in tools if tool["name"] in allowed]

    def call_tool(
        self,
        tool_name: str,
        arguments: dict[str, object] | None = None,
        *,
        read_only: bool = False,
    ) -> dict[str, object]:
        """Call one advertised tool after enforcing local execution policy."""
        requested = tool_name.strip()
        if not requested:
            raise McpGatewayRefused("MCP gateway tool_name is required.")

        def matches(names: tuple[str, ...] | set[str]) -> set[str]:
            return {
                name
                for name in names
                if name == requested or public_tool_name(name, self.config.auth_token) == requested
            }

        if self.config.allowed_tools and len(matches(self.config.allowed_tools)) != 1:
            raise McpGatewayRefused("MCP gateway tool is not allowed or its name is ambiguous.")
        if read_only and len(matches(self.config.read_only_tools)) != 1:
            raise McpGatewayRefused(
                "MCP gateway tool is not certified read-only or its name is ambiguous."
            )

        advertised_names = {tool["name"] for tool in self.list_all_tools()}
        candidates = matches(advertised_names)
        if not candidates:
            raise McpGatewayRefused("MCP gateway tool is not advertised by the server.")
        if len(candidates) != 1:
            raise McpGatewayRefused("MCP gateway tool name is ambiguous.")
        name = next(iter(candidates))
        if self.config.allowed_tools and name not in self.config.allowed_tools:
            raise McpGatewayRefused("MCP gateway tool is not allowed.")
        if read_only and name not in self.config.read_only_tools:
            raise McpGatewayRefused("MCP gateway tool is not certified read-only.")

        try:
            result = call_mcp_tool(
                self.config,
                name,
                arguments,
                timeout_call=False,
                timeout_entire_operation=True,
                response_byte_limit=MCP_GATEWAY_TOOL_RESPONSE_MAX_BYTES,
                **self._session_options(),
            )
        except Exception as exc:
            error = safe_request_error(
                exc,
                auth_token=self.config.auth_token,
                timeout_seconds=self.config.timeout_seconds,
                mutation_outcome_unknown=not read_only,
            )
        else:
            return safe_tool_result(result, auth_token=self.config.auth_token)
        raise error
