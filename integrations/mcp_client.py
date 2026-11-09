"""Shared transport and result handling for configured MCP integrations."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Coroutine, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from typing import TYPE_CHECKING, NotRequired, Protocol, Unpack, cast

import httpx
import mcp_types as types
from typing_extensions import TypedDict

from config.constants.mcp import MCP_NO_COLOR_ENV, MCP_TERMINAL_DUMB_VALUE, MCP_TERMINAL_ENV
from integrations.mcp_streamable_http_compat import streamable_http_client
from integrations.mcp_transport import McpTransportMode

if TYPE_CHECKING:
    from mcp.client.session import ClientSession  # type: ignore[import-not-found]


class McpClientConfig(Protocol):
    """Connection settings required by the shared MCP client."""

    @property
    def mode(self) -> McpTransportMode:
        """Return the selected MCP transport."""

    @property
    def url(self) -> str:
        """Return the configured HTTP endpoint."""

    @property
    def command(self) -> str:
        """Return the configured stdio command."""

    @property
    def args(self) -> tuple[str, ...]:
        """Return arguments for the stdio command."""

    @property
    def timeout_seconds(self) -> float:
        """Return the operation timeout in seconds."""

    @property
    def request_headers(self) -> dict[str, str]:
        """Return headers for HTTP-based MCP transports."""


class McpSessionOptions(TypedDict):
    """Vendor-specific values needed to establish an MCP session."""

    session_url: str
    stdio_env: Mapping[str, str]
    integration_name: str
    config_env_name: str
    sse_url_hint: NotRequired[str | None]
    streamable_url_hint: NotRequired[str | None]


@asynccontextmanager
async def open_mcp_session(
    config: McpClientConfig,
    *,
    session_url: str,
    stdio_env: Mapping[str, str],
    integration_name: str,
    config_env_name: str,
    sse_url_hint: str | None = None,
    streamable_url_hint: str | None = None,
) -> AsyncIterator[ClientSession]:
    """Open and initialize an MCP session for one configured integration."""
    from mcp.client.session import ClientSession  # type: ignore[import-not-found]
    from mcp.client.sse import sse_client  # type: ignore[import-not-found]
    from mcp.client.stdio import (  # type: ignore[import-not-found]
        StdioServerParameters,
        stdio_client,
    )

    stack = AsyncExitStack()
    try:
        if config.mode == McpTransportMode.STDIO:
            if not config.command:
                raise ValueError(
                    f"Invalid {integration_name} MCP config: mode=stdio requires command "
                    f"(set {config_env_name}_COMMAND or pass command in config)."
                )
            server_params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env={
                    **os.environ,
                    MCP_NO_COLOR_ENV: "1",
                    MCP_TERMINAL_ENV: MCP_TERMINAL_DUMB_VALUE,
                    **stdio_env,
                },
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))
        elif config.mode == McpTransportMode.SSE:
            if not config.url:
                hint = f", e.g. {sse_url_hint}" if sse_url_hint else ""
                raise ValueError(
                    f"Invalid {integration_name} MCP config: mode=sse requires url "
                    f"(set {config_env_name}_URL{hint})."
                )
            read_stream, write_stream = await stack.enter_async_context(
                sse_client(
                    session_url,
                    headers=config.request_headers,
                    timeout=config.timeout_seconds,
                    sse_read_timeout=max(60.0, config.timeout_seconds),
                )
            )
        elif config.mode == McpTransportMode.STREAMABLE_HTTP:
            if not config.url:
                hint = f", e.g. {streamable_url_hint}" if streamable_url_hint else ""
                raise ValueError(
                    f"Invalid {integration_name} MCP config: mode=streamable-http requires url "
                    f"(set {config_env_name}_URL{hint})."
                )
            read_timeout = max(60.0, config.timeout_seconds)
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=config.request_headers,
                    timeout=httpx.Timeout(config.timeout_seconds, read=read_timeout),
                )
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    session_url,
                    http_client=http_client,
                    headers=config.request_headers,
                    timeout=config.timeout_seconds,
                    sse_read_timeout=read_timeout,
                )
            )
        else:
            raise ValueError(
                f"Unsupported {integration_name} MCP mode '{config.mode}'. "
                "Supported modes: stdio, sse, streamable-http."
            )

        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        yield session
    finally:
        await stack.aclose()


def run_async(coro: Coroutine[object, object, object]) -> object:
    """Run a coroutine while closing it if event-loop startup fails."""
    try:
        return asyncio.run(coro)
    except Exception:
        close = getattr(coro, "close", None)
        if callable(close):
            close()
        raise


def root_cause_message(exc: BaseException, *, timeout_message: str) -> str:
    """Return a useful message from a chained or grouped exception."""
    if isinstance(exc, BaseExceptionGroup) and exc.exceptions:
        return root_cause_message(exc.exceptions[0], timeout_message=timeout_message)
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, BaseException):
        return root_cause_message(cause, timeout_message=timeout_message)
    context = getattr(exc, "__context__", None)
    if isinstance(context, BaseException):
        return root_cause_message(context, timeout_message=timeout_message)
    if isinstance(exc, TimeoutError):
        return timeout_message
    return str(exc).strip() or exc.__class__.__name__


def tool_result_to_dict(result: types.CallToolResult) -> dict[str, object]:
    """Normalize an MCP tool result into the shared integration payload shape."""
    text_parts: list[str] = []
    content_items: list[dict[str, str]] = []
    for item in result.content:
        if isinstance(item, types.TextContent):
            text_parts.append(item.text)
            content_items.append({"type": "text", "text": item.text})
        elif isinstance(item, types.EmbeddedResource):
            resource = item.resource
            if isinstance(resource, types.TextResourceContents):
                content_items.append(
                    {"type": "resource_text", "uri": str(resource.uri), "text": resource.text}
                )
                text_parts.append(resource.text)
            elif isinstance(resource, types.BlobResourceContents):
                content_items.append(
                    {
                        "type": "resource_blob",
                        "uri": str(resource.uri),
                        "mime_type": resource.mime_type or "",
                    }
                )
        else:
            content_items.append({"type": getattr(item, "type", "unknown")})
    return {
        "is_error": bool(result.is_error),
        "text": "\n".join(part.strip() for part in text_parts if part.strip()).strip(),
        "content": content_items,
        "structured_content": result.structured_content,
    }


async def _list_tools_async(
    config: McpClientConfig,
    **session_options: Unpack[McpSessionOptions],
) -> list[types.Tool]:
    async with open_mcp_session(config, **session_options) as session:
        return list((await session.list_tools()).tools)


def list_mcp_tools(
    config: McpClientConfig,
    *,
    timeout_entire_operation: bool = False,
    **session_options: Unpack[McpSessionOptions],
) -> list[types.Tool]:
    """List MCP tools, optionally bounding connection setup as well as the RPC."""
    operation = _list_tools_async(config, **session_options)
    if timeout_entire_operation:
        operation = asyncio.wait_for(operation, timeout=config.timeout_seconds)
    return cast(list[types.Tool], run_async(operation))


async def _call_tool_async(
    config: McpClientConfig,
    tool_name: str,
    arguments: dict[str, object] | None,
    *,
    timeout_call: bool,
    **session_options: Unpack[McpSessionOptions],
) -> dict[str, object]:
    async with open_mcp_session(config, **session_options) as session:
        call = session.call_tool(tool_name, arguments or {})
        result = (
            await asyncio.wait_for(call, timeout=config.timeout_seconds)
            if timeout_call
            else await call
        )
        payload = tool_result_to_dict(result)
        payload["tool"] = tool_name
        payload["arguments"] = arguments or {}
        return payload


def call_mcp_tool(
    config: McpClientConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
    *,
    timeout_call: bool,
    timeout_entire_operation: bool = False,
    **session_options: Unpack[McpSessionOptions],
) -> dict[str, object]:
    """Call an MCP tool and normalize its result."""
    operation = _call_tool_async(
        config,
        tool_name,
        arguments,
        timeout_call=timeout_call,
        **session_options,
    )
    if timeout_entire_operation:
        operation = asyncio.wait_for(operation, timeout=config.timeout_seconds)
    return cast(dict[str, object], run_async(operation))
