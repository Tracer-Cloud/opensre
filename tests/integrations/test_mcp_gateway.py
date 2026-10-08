"""Unit tests for the generic MCP gateway integration."""

from __future__ import annotations

from http import HTTPStatus
from unittest.mock import patch

import httpx
import mcp_types as types
import pytest
from pydantic import ValidationError

from config.constants.mcp_gateway import MCP_GATEWAY_TOOL_RESPONSE_MAX_BYTES
from integrations.mcp_client import McpResponseTooLargeError, McpToolCallOutcomeUnknownError
from integrations.mcp_gateway import (
    McpGatewayClient,
    McpGatewayConfig,
    McpGatewayRefused,
    McpGatewayRequestError,
    build_mcp_gateway_config,
    describe_mcp_gateway_error,
    validate_mcp_gateway_config,
)
from integrations.mcp_gateway.redaction import public_tool_name, redacted_text_preview
from integrations.mcp_gateway.setup import MCP_GATEWAY_SETUP
from integrations.registry import service_key


class TestMcpGatewayConfig:
    def test_normalizes_auth_and_comma_separated_tool_sets(self) -> None:
        config = McpGatewayConfig(
            url="https://mcp.example.test/mcp/",
            auth_token="Bearer secret",
            allowed_tools=" status, restart_service, status ",
            read_only_tools="status",
        )

        assert config.url == "https://mcp.example.test/mcp/"
        assert config.auth_token == "secret"
        assert config.allowed_tools == ("restart_service", "status")
        assert config.read_only_tools == ("status",)
        assert config.request_headers == {"Authorization": "Bearer secret"}

    def test_accepts_loopback_http(self) -> None:
        assert McpGatewayConfig(url="http://127.0.0.1:8765/mcp").is_configured is True

    def test_url_normalization_preserves_query_value_trailing_slashes(self) -> None:
        config = McpGatewayConfig(url="https://mcp.example.test/mcp/?route=/api/")

        assert config.url == "https://mcp.example.test/mcp/?route=/api/"

    def test_url_normalization_preserves_repeated_trailing_path_slashes(self) -> None:
        config = McpGatewayConfig(url="https://mcp.example.test/tenant//")

        assert config.url == "https://mcp.example.test/tenant//"

    def test_rejects_non_loopback_http(self) -> None:
        with pytest.raises(ValidationError, match="must use https"):
            McpGatewayConfig(url="http://mcp.example.test/mcp")

    def test_read_only_tools_must_be_allowed_when_allowlist_is_set(self) -> None:
        with pytest.raises(ValidationError, match="read-only tools must also appear"):
            McpGatewayConfig(
                url="https://mcp.example.test/mcp",
                allowed_tools=("status",),
                read_only_tools=("restart_service",),
            )

    def test_rejects_malformed_or_unknown_policy_fields(self) -> None:
        with pytest.raises(ValidationError):
            McpGatewayConfig(
                url="https://mcp.example.test/mcp",
                allowed_tools={"status": True},
            )
        with pytest.raises(ValidationError, match="allowed_tool"):
            build_mcp_gateway_config(
                {
                    "url": "https://mcp.example.test/mcp",
                    "allowed_tool": "status",
                }
            )


def test_client_lists_only_allowlisted_tools() -> None:
    config = McpGatewayConfig(
        url="https://mcp.example.test/mcp",
        allowed_tools=("status",),
    )
    with patch(
        "integrations.mcp_gateway.client.list_mcp_tools",
        return_value=[
            types.Tool(name="status", description="Status", input_schema={}),
            types.Tool(name="restart_service", description="Restart", input_schema={}),
        ],
    ):
        tools = McpGatewayClient(config).list_tools()

    assert [tool["name"] for tool in tools] == ["status"]


def test_client_refuses_disallowed_tool_before_network() -> None:
    config = McpGatewayConfig(
        url="https://mcp.example.test/mcp",
        allowed_tools=("status",),
    )
    with (
        patch("integrations.mcp_gateway.client.list_mcp_tools") as list_tools,
        pytest.raises(McpGatewayRefused, match="not allowed"),
    ):
        McpGatewayClient(config).call_tool("restart_service", {})
    list_tools.assert_not_called()


def test_client_refuses_unknown_advertised_tool_before_invocation() -> None:
    config = McpGatewayConfig(url="https://mcp.example.test/mcp")
    with (
        patch("integrations.mcp_gateway.client.list_mcp_tools", return_value=[]),
        patch("integrations.mcp_gateway.client.call_mcp_tool") as call_tool,
        pytest.raises(McpGatewayRefused, match="not advertised"),
    ):
        McpGatewayClient(config).call_tool("missing", {})
    call_tool.assert_not_called()


def test_client_caps_tool_response_before_protocol_parsing() -> None:
    config = McpGatewayConfig(url="https://mcp.example.test/mcp")
    with (
        patch(
            "integrations.mcp_gateway.client.list_mcp_tools",
            return_value=[types.Tool(name="status", input_schema={})],
        ),
        patch(
            "integrations.mcp_gateway.client.call_mcp_tool",
            return_value={"tool": "status", "content": []},
        ) as call_tool,
    ):
        McpGatewayClient(config).call_tool("status")

    assert call_tool.call_args.kwargs["response_byte_limit"] == (
        MCP_GATEWAY_TOOL_RESPONSE_MAX_BYTES
    )


def test_oversized_mutation_response_reports_unknown_outcome() -> None:
    config = McpGatewayConfig(url="https://mcp.example.test/mcp")
    failure = McpToolCallOutcomeUnknownError("MCP tool call failed after request dispatch began")
    failure.__cause__ = McpResponseTooLargeError(
        "MCP HTTP response exceeded the configured byte limit"
    )
    with (
        patch(
            "integrations.mcp_gateway.client.list_mcp_tools",
            return_value=[types.Tool(name="restart_service", input_schema={})],
        ),
        patch("integrations.mcp_gateway.client.call_mcp_tool", side_effect=failure),
        pytest.raises(McpGatewayRequestError, match="outcome is unknown.*Do not retry"),
    ):
        McpGatewayClient(config).call_tool("restart_service")


def test_disconnected_mutation_reports_unknown_outcome() -> None:
    config = McpGatewayConfig(url="https://mcp.example.test/mcp")
    failure = McpToolCallOutcomeUnknownError("MCP tool call failed after request dispatch began")
    failure.__cause__ = httpx.ReadError("connection reset")
    with (
        patch(
            "integrations.mcp_gateway.client.list_mcp_tools",
            return_value=[types.Tool(name="restart_service", input_schema={})],
        ),
        patch(
            "integrations.mcp_gateway.client.call_mcp_tool",
            side_effect=failure,
        ),
        pytest.raises(McpGatewayRequestError, match="outcome is unknown.*Do not retry"),
    ):
        McpGatewayClient(config).call_tool("restart_service")


def test_pre_dispatch_mutation_failure_does_not_report_unknown_outcome() -> None:
    config = McpGatewayConfig(url="https://mcp.example.test/mcp")
    with (
        patch(
            "integrations.mcp_gateway.client.list_mcp_tools",
            return_value=[types.Tool(name="restart_service", input_schema={})],
        ),
        patch(
            "integrations.mcp_gateway.client.call_mcp_tool",
            side_effect=httpx.ReadError("initialization failed"),
        ),
        pytest.raises(McpGatewayRequestError, match="ReadError") as error,
    ):
        McpGatewayClient(config).call_tool("restart_service")

    assert "outcome is unknown" not in str(error.value)


def test_client_reports_the_effective_timeout() -> None:
    config = McpGatewayConfig(
        url="https://mcp.example.test/mcp",
        timeout_seconds=7.5,
    )
    with (
        patch("integrations.mcp_gateway.client.list_mcp_tools", side_effect=TimeoutError),
        pytest.raises(McpGatewayRequestError, match="7.5 seconds"),
    ):
        McpGatewayClient(config).list_all_tools()


def test_validation_requires_configured_names_to_be_advertised() -> None:
    config = build_mcp_gateway_config(
        {
            "url": "https://mcp.example.test/mcp",
            "allowed_tools": "status,restart_service",
            "read_only_tools": "status",
        }
    )
    with patch.object(
        McpGatewayClient,
        "list_all_tools",
        return_value=[{"name": "status", "description": "Status", "input_schema": {}}],
    ):
        result = validate_mcp_gateway_config(config)

    assert result.ok is False
    assert "restart_service" in result.detail


def test_validation_reports_read_only_and_approval_required_counts() -> None:
    config = build_mcp_gateway_config(
        {
            "url": "https://mcp.example.test/mcp",
            "read_only_tools": "status",
        }
    )
    with patch.object(
        McpGatewayClient,
        "list_all_tools",
        return_value=[
            {"name": "status", "description": "Status", "input_schema": {}},
            {"name": "restart_service", "description": "Restart", "input_schema": {}},
        ],
    ):
        result = validate_mcp_gateway_config(config)

    assert result.ok is True
    assert "discovered 2 tool(s)" in result.detail
    assert "1 read-only" in result.detail
    assert "1 approval-required" in result.detail


def test_auth_error_description_does_not_expose_response_or_token() -> None:
    request = httpx.Request("GET", "https://mcp.example.test/mcp")
    response = httpx.Response(
        HTTPStatus.UNAUTHORIZED,
        request=request,
        text="Bearer super-secret challenge",
        headers={"WWW-Authenticate": "Bearer super-secret"},
    )
    error = RuntimeError("outer")
    error.__cause__ = httpx.HTTPStatusError(
        "Bearer super-secret", request=request, response=response
    )

    detail = describe_mcp_gateway_error(error, auth_token="super-secret")

    assert "MCP_GATEWAY_AUTH_TOKEN" in detail
    assert "super-secret" not in detail


def test_generic_mcp_server_error_gets_auth_hint() -> None:
    detail = describe_mcp_gateway_error(
        RuntimeError("Server returned an error response"),
        auth_token="",
    )

    assert "MCP_GATEWAY_AUTH_TOKEN" in detail


def test_mcp_not_found_error_gets_endpoint_hint() -> None:
    detail = describe_mcp_gateway_error(RuntimeError("Not Found"), auth_token="")

    assert "MCP_GATEWAY_URL" in detail
    assert "/mcp" in detail


def test_timeout_description_uses_effective_configured_value() -> None:
    detail = describe_mcp_gateway_error(
        TimeoutError(),
        auth_token="",
        timeout_seconds=7.5,
    )

    assert detail == "MCP gateway operation timed out after 7.5 seconds."


def test_registry_resolves_public_aliases() -> None:
    assert service_key("mcp_gateway") == "mcp_gateway"
    assert service_key("remote_mcp") == "mcp_gateway"
    assert service_key("mcp-gateway") == "mcp_gateway"
    assert service_key("mcp gateway") == "mcp_gateway"


def test_setup_declares_public_environment_variables() -> None:
    fields = {field.name: field for field in MCP_GATEWAY_SETUP.fields}

    assert MCP_GATEWAY_SETUP.service == "mcp_gateway"
    assert fields["url"].env_var == "MCP_GATEWAY_URL"
    assert fields["auth_token"].env_var == "MCP_GATEWAY_AUTH_TOKEN"
    assert fields["auth_token"].secret is True
    assert fields["allowed_tools"].env_var == "MCP_GATEWAY_ALLOWED_TOOLS"
    assert fields["read_only_tools"].env_var == "MCP_GATEWAY_READ_ONLY_TOOLS"


def test_catalog_classifies_mcp_gateway_record() -> None:
    from integrations.catalog import classify_integrations

    resolved = classify_integrations(
        [
            {
                "id": "gateway",
                "service": "mcp_gateway",
                "status": "active",
                "credentials": {
                    "url": "http://127.0.0.1:8765/mcp",
                    "allowed_tools": "status,restart_service",
                    "read_only_tools": "status",
                },
            }
        ]
    )

    assert resolved["mcp_gateway"].allowed_tools == ("restart_service", "status")


def test_catalog_loads_gateway_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from integrations.catalog import load_env_integrations, resolve_effective_integrations

    monkeypatch.setenv("MCP_GATEWAY_URL", "http://127.0.0.1:8765/mcp")
    monkeypatch.setenv("MCP_GATEWAY_ALLOWED_TOOLS", "status,restart_service")
    monkeypatch.setenv("MCP_GATEWAY_READ_ONLY_TOOLS", "status")
    monkeypatch.delenv("MCP_GATEWAY_AUTH_TOKEN", raising=False)

    records = load_env_integrations()
    record = next(item for item in records if item["service"] == "mcp_gateway")

    assert record["credentials"]["read_only_tools"] == ("status",)
    assert "mcp_gateway" in resolve_effective_integrations(env_integrations=records)


def test_successful_payloads_scrub_the_configured_token() -> None:
    token = "sample-token"
    config = McpGatewayConfig(url="https://mcp.example.test/mcp", auth_token=token)
    response = {
        "is_error": False,
        "tool": "status",
        "arguments": {},
        "text": f"Echo Bearer {token}",
        "structured_content": {"items": [{token: token}], "count": 1},
        "content": [{"type": "text", "text": token}],
    }
    with (
        patch(
            "integrations.mcp_gateway.client.list_mcp_tools",
            return_value=[
                types.Tool(name="status", description=f"Diagnostic {token}", input_schema={})
            ],
        ),
        patch("integrations.mcp_gateway.client.call_mcp_tool", return_value=response),
    ):
        client = McpGatewayClient(config)
        result = client.call_tool("status")
    assert token not in repr(result)
    assert result["structured_content"] == {"items": [{"[redacted]": "[redacted]"}], "count": 1}
    assert token in repr(response)


def test_successful_payloads_redact_unconfigured_gitlab_tokens() -> None:
    token = "glpat-" + "A" * 32
    config = McpGatewayConfig(url="https://mcp.example.test/mcp")
    response = {
        "is_error": False,
        "tool": "status",
        "arguments": {},
        "structured_content": {"header": token},
        "content": [],
    }
    with (
        patch(
            "integrations.mcp_gateway.client.list_mcp_tools",
            return_value=[types.Tool(name="status", input_schema={})],
        ),
        patch("integrations.mcp_gateway.client.call_mcp_tool", return_value=response),
    ):
        result = McpGatewayClient(config).call_tool("status")

    assert token not in repr(result)


def test_long_auth_token_does_not_erase_unrelated_preview() -> None:
    auth_token = "header." + "A" * 1_024 + ".signature"
    preview = redacted_text_preview(
        "Unrelated diagnostic description " * 20,
        auth_token,
        max_chars=128,
    )

    assert preview != "[oversized value omitted]"
    assert preview.endswith(" [truncated]")


def test_long_auth_token_occurrence_omits_unsafe_preview() -> None:
    auth_token = "header." + "A" * 1_024 + ".signature"

    assert (
        redacted_text_preview(f"unsafe {auth_token} value", auth_token, max_chars=128)
        == "[oversized value omitted]"
    )


def test_alias_collision_cannot_select_another_advertised_tool() -> None:
    config = McpGatewayConfig(
        url="https://mcp.example.test/mcp",
        auth_token="status",
        allowed_tools=("service_status",),
        read_only_tools=("service_status",),
    )
    alias = public_tool_name("service_status", config.auth_token)
    with (
        patch(
            "integrations.mcp_gateway.client.list_mcp_tools",
            return_value=[
                types.Tool(name="service_status", input_schema={}),
                types.Tool(name=alias, input_schema={}),
            ],
        ),
        patch("integrations.mcp_gateway.client.call_mcp_tool") as call,
        pytest.raises(McpGatewayRefused, match="ambiguous"),
    ):
        McpGatewayClient(config).call_tool(alias, read_only=True)
    call.assert_not_called()


@pytest.mark.parametrize(
    "url",
    [
        "https://user:password@example.test/mcp",
        "https://user@example.test/mcp",
        "https://@example.test/mcp",
    ],
)
def test_gateway_rejects_url_userinfo_before_any_connection(url: str) -> None:
    with pytest.raises(ValueError, match="must not contain credentials") as error:
        McpGatewayConfig(url=url)
    assert url not in str(error.value)
