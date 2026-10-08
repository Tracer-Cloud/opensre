"""Shared transport and result handling for configured MCP integrations."""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, NotRequired, Protocol, Unpack, cast

import httpx
import mcp_types as types
from typing_extensions import TypedDict

from config.constants.mcp import (
    MCP_NO_COLOR_ENV,
    MCP_TERMINAL_DUMB_VALUE,
    MCP_TERMINAL_ENV,
    MCP_TOOL_LIST_MAX_PAGES,
    MCP_TOOL_LIST_MAX_RESPONSE_BYTES,
    MCP_TOOL_LIST_MAX_SERIALIZED_CHARS,
    MCP_TOOL_LIST_MAX_TOOLS,
)
from infrastructure.text import json_size_up_to
from integrations.mcp_streamable_http_compat import streamable_http_client
from integrations.mcp_transport import McpTransportMode

if TYPE_CHECKING:
    from mcp.client.session import ClientSession  # type: ignore[import-not-found]


class McpToolCallOutcomeUnknownError(RuntimeError):
    """Signal that transport failed after an MCP tool request began."""


@dataclass
class _ToolCallState:
    request_started: bool = False


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


class McpResponseTooLargeError(httpx.StreamError):
    """Raised before an MCP HTTP response can exceed its byte budget."""


class _BoundedResponseStream(httpx.AsyncByteStream):
    """Stop consuming an HTTP response once its wire-byte budget is exhausted."""

    def __init__(self, stream: httpx.AsyncByteStream, limit: int) -> None:
        self._stream = stream
        self._limit = limit
        self._received = 0

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for chunk in self._stream:
            self._received += len(chunk)
            if self._received > self._limit:
                await self.aclose()
                raise McpResponseTooLargeError(
                    "MCP HTTP response exceeded the configured byte limit"
                )
            yield chunk

    async def aclose(self) -> None:
        await self._stream.aclose()


def _response_size_hook(limit: int) -> Callable[[httpx.Response], Awaitable[None]]:
    async def _enforce(response: httpx.Response) -> None:
        if response.request.method not in {"GET", "POST"}:
            return
        content_encoding = response.headers.get("content-encoding", "identity").lower()
        if content_encoding not in {"", "identity"}:
            await response.aclose()
            raise McpResponseTooLargeError(
                "MCP HTTP response compression is disabled while enforcing the byte limit"
            )
        content_length = response.headers.get("content-length")
        try:
            declared_length = int(content_length) if content_length is not None else None
        except ValueError:
            declared_length = None
        if declared_length is not None and declared_length > limit:
            await response.aclose()
            raise McpResponseTooLargeError("MCP HTTP response exceeded the configured byte limit")
        response.stream = _BoundedResponseStream(
            cast(httpx.AsyncByteStream, response.stream), limit
        )

    return _enforce


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
    response_byte_limit: int | None = None,
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
            headers = dict(config.request_headers)
            event_hooks = None
            if response_byte_limit is not None:
                headers["Accept-Encoding"] = "identity"
                event_hooks = {"response": [_response_size_hook(response_byte_limit)]}
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=headers,
                    timeout=httpx.Timeout(config.timeout_seconds, read=read_timeout),
                    event_hooks=event_hooks,
                )
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    session_url,
                    http_client=http_client,
                    headers=headers,
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
    async with open_mcp_session(
        config,
        response_byte_limit=MCP_TOOL_LIST_MAX_RESPONSE_BYTES,
        **session_options,
    ) as session:
        tools: list[types.Tool] = []
        serialized_chars = 0
        cursor: str | None = None
        seen_cursors: set[str] = set()
        for _ in range(MCP_TOOL_LIST_MAX_PAGES):
            params = types.PaginatedRequestParams(cursor=cursor) if cursor is not None else None
            page = await session.list_tools(params=params)
            for tool in page.tools:
                if len(tools) >= MCP_TOOL_LIST_MAX_TOOLS:
                    raise RuntimeError(
                        "MCP tool listing exceeded the cumulative tool-count safety limit"
                    )
                remaining_chars = MCP_TOOL_LIST_MAX_SERIALIZED_CHARS - serialized_chars
                descriptor_chars = json_size_up_to(tool, remaining_chars)
                if descriptor_chars is None or descriptor_chars > remaining_chars:
                    raise RuntimeError(
                        "MCP tool listing exceeded the cumulative serialized-size safety limit"
                    )
                serialized_chars += descriptor_chars
                tools.append(tool)
            cursor = page.next_cursor
            if cursor is None:
                return tools
            if cursor in seen_cursors:
                raise RuntimeError("MCP server returned a repeated pagination cursor")
            seen_cursors.add(cursor)
        raise RuntimeError(
            f"MCP tool listing exceeded the {MCP_TOOL_LIST_MAX_PAGES}-page safety limit"
        )


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
    response_byte_limit: int | None,
    call_state: _ToolCallState,
    **session_options: Unpack[McpSessionOptions],
) -> dict[str, object]:
    async with open_mcp_session(
        config,
        response_byte_limit=response_byte_limit,
        **session_options,
    ) as session:
        call_state.request_started = True
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
    response_byte_limit: int | None = None,
    **session_options: Unpack[McpSessionOptions],
) -> dict[str, object]:
    """Call an MCP tool and normalize its result."""
    call_state = _ToolCallState()
    operation = _call_tool_async(
        config,
        tool_name,
        arguments,
        timeout_call=timeout_call,
        response_byte_limit=response_byte_limit,
        call_state=call_state,
        **session_options,
    )
    if timeout_entire_operation:
        operation = asyncio.wait_for(operation, timeout=config.timeout_seconds)
    try:
        return cast(dict[str, object], run_async(operation))
    except Exception as exc:
        if call_state.request_started:
            raise McpToolCallOutcomeUnknownError(
                "MCP tool call failed after request dispatch began"
            ) from exc
        raise
