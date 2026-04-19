"""Shared OpenClaw MCP integration helpers.

OpenClaw is an AI coding assistant that communicates via the Model Context Protocol (MCP).
This module centralizes OpenClaw MCP configuration, validation, and tool-calling so the
onboarding wizard, verify CLI, and investigation flows all share the same transport logic.

Supported transports:
  - streamable-http  (default) — HTTP-based MCP via Streamable HTTP
  - sse              — Server-Sent Events MCP transport
  - stdio            — subprocess-based MCP (local dev)
"""

from __future__ import annotations

import asyncio
import os
from collections.abc import AsyncIterator, Coroutine, Mapping
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypedDict, cast
from urllib.parse import urlparse

import httpx
from mcp import ClientSession, StdioServerParameters, types  # type: ignore[import-not-found]
from mcp.client.sse import sse_client  # type: ignore[import-not-found]
from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]
from pydantic import Field, field_validator, model_validator

from app.integrations.mcp_streamable_http_compat import streamable_http_client
from app.strict_config import StrictConfigModel

DEFAULT_OPENCLAW_MCP_MODE: Literal["streamable-http", "sse", "stdio"] = "streamable-http"
_OPENCLAW_CONTROL_UI_HOSTS = frozenset({"127.0.0.1", "localhost", "0.0.0.0"})
_OPENCLAW_CONTROL_UI_PORT = 18789
_OPENCLAW_STDIO_COMMAND = "openclaw"
_OPENCLAW_STDIO_ARGS = ("mcp", "serve")
class OpenClawToolDescriptor(TypedDict):
    """A tool exposed by the OpenClaw MCP bridge."""

    name: str
    description: str
    input_schema: object | None


class OpenClawContentItem(TypedDict, total=False):
    """Normalized content item returned by an MCP tool call."""

    type: str
    text: str
    uri: str
    mime_type: str


class OpenClawToolCallResult(TypedDict, total=False):
    """Normalized response from an OpenClaw MCP tool call."""

    is_error: bool
    text: str
    content: list[OpenClawContentItem]
    structured_content: object | None
    tool: str
    arguments: dict[str, object]


class OpenClawConfig(StrictConfigModel):
    """Normalized OpenClaw MCP connection settings."""

    url: str = ""
    mode: Literal["stdio", "sse", "streamable-http"] = DEFAULT_OPENCLAW_MCP_MODE
    auth_token: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: float = Field(default=15.0, gt=0)
    integration_id: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: object) -> str:
        return str(value or "").strip().rstrip("/")

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: object) -> str:
        normalized = str(value or DEFAULT_OPENCLAW_MCP_MODE).strip().lower()
        return normalized or DEFAULT_OPENCLAW_MCP_MODE

    @field_validator("auth_token", mode="before")
    @classmethod
    def _normalize_auth_token(cls, value: object) -> str:
        token = str(value or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        return token

    @field_validator("command", mode="before")
    @classmethod
    def _normalize_command(cls, value: object) -> str:
        return str(value or "").strip()

    @field_validator("args", mode="before")
    @classmethod
    def _normalize_args(cls, value: object) -> tuple[str, ...]:
        if value is None:
            return ()
        if not isinstance(value, (list, tuple, set)):
            return ()
        return tuple(str(arg).strip() for arg in value if str(arg).strip())

    @field_validator("headers", mode="before")
    @classmethod
    def _normalize_headers(cls, value: object) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(k): str(v).strip() for k, v in value.items() if str(v).strip()}

    @model_validator(mode="after")
    def _validate_transport_requirements(self) -> OpenClawConfig:
        if self.mode == "stdio" and not self.command:
            raise ValueError("OpenClaw MCP mode 'stdio' requires a non-empty command.")
        if self.mode != "stdio" and not self.url:
            raise ValueError(
                f"OpenClaw MCP mode '{self.mode}' requires a non-empty url."
            )
        return self

    @property
    def is_configured(self) -> bool:
        if self.mode == "stdio":
            return bool(self.command)
        return bool(self.url)

    @property
    def request_headers(self) -> dict[str, str]:
        headers = {k: v for k, v in self.headers.items() if v}
        if self.auth_token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers


@dataclass(frozen=True)
class OpenClawValidationResult:
    """Result of validating an OpenClaw MCP integration."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()


def _is_probable_openclaw_control_ui_url(url: str) -> bool:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").strip().lower()
    if host not in _OPENCLAW_CONTROL_UI_HOSTS:
        return False

    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80

    normalized_path = parsed.path.rstrip("/")
    return port == _OPENCLAW_CONTROL_UI_PORT and normalized_path == ""


def _dedupe_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for item in items:
        normalized = item.strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique


def _uses_openclaw_cli_mcp_bridge(config: OpenClawConfig) -> bool:
    command_name = Path(config.command or "").name.lower()
    return (
        config.mode == "stdio"
        and command_name == "openclaw"
        and tuple(config.args[:2]) == _OPENCLAW_STDIO_ARGS
    )


def _looks_like_openclaw_gateway_unavailable(messages: list[str]) -> bool:
    indicators = (
        "connection closed",
        "econnrefused",
        "connect failed",
        "could not connect",
        "closed before connect",
    )
    return any(indicator in message.lower() for message in messages for indicator in indicators)


def _describe_exception(err: BaseException) -> list[str]:
    if isinstance(err, BaseExceptionGroup):
        messages: list[str] = []
        for sub_exception in err.exceptions:
            messages.extend(_describe_exception(sub_exception))
        return messages

    if isinstance(err, FileNotFoundError):
        command = err.filename or str(err).split(":", 1)[0].strip()
        return [f"Command not found: {command or 'unknown command'}"]

    if isinstance(err, httpx.HTTPStatusError):
        return [
            f"HTTP {err.response.status_code} from {err.request.method} {err.request.url}"
        ]

    if isinstance(err, httpx.ConnectError):
        request = getattr(err, "request", None)
        if request is not None:
            return [f"Could not connect to {request.url}: {err}"]
        return [str(err) or err.__class__.__name__]

    return [str(err).strip() or err.__class__.__name__]


def describe_openclaw_error(
    err: BaseException,
    config: OpenClawConfig,
) -> str:
    messages = _dedupe_preserving_order(_describe_exception(err))
    detail = "; ".join(messages) if messages else (str(err).strip() or err.__class__.__name__)
    hints: list[str] = []

    if config.mode != "stdio" and _is_probable_openclaw_control_ui_url(config.url):
        hints.append(
            "The local OpenClaw URL on port 18789 is the Control UI/Gateway, not the MCP "
            f"bridge. Use mode `stdio` with command `{_OPENCLAW_STDIO_COMMAND}` and args "
            f"`{' '.join(_OPENCLAW_STDIO_ARGS)}`."
        )

    if config.mode == "stdio" and config.command == "openclaw-mcp":
        hints.append(
            "OpenClaw's current MCP bridge is exposed via `openclaw mcp serve`, not "
            "`openclaw-mcp`."
        )

    if config.mode == "stdio" and any(message.startswith("Command not found:") for message in messages):
        hints.append(
            "Install the OpenClaw CLI or set `OPENCLAW_MCP_COMMAND` to the full executable "
            "path."
        )

    if _uses_openclaw_cli_mcp_bridge(config) and _looks_like_openclaw_gateway_unavailable(messages):
        hints.append(
            "The `openclaw mcp serve` bridge needs a running OpenClaw Gateway. "
            "Check `openclaw gateway status`, then start it with `openclaw gateway run` "
            "(foreground) or `openclaw gateway install` followed by `openclaw gateway start`."
        )

    if hints:
        return f"{detail} Hint: {' '.join(hints)}"
    return detail


def build_openclaw_config(raw: Mapping[str, object] | None) -> OpenClawConfig:
    """Build a normalized OpenClaw config object from env/store data."""
    return OpenClawConfig.model_validate(raw or {})


def openclaw_config_from_env() -> OpenClawConfig | None:
    """Load an OpenClaw MCP config from environment variables."""
    mode = os.getenv("OPENCLAW_MCP_MODE", DEFAULT_OPENCLAW_MCP_MODE).strip().lower()
    url = os.getenv("OPENCLAW_MCP_URL", "").strip()
    command = os.getenv("OPENCLAW_MCP_COMMAND", "").strip()
    auth_token = os.getenv("OPENCLAW_MCP_AUTH_TOKEN", "").strip()
    args_env = os.getenv("OPENCLAW_MCP_ARGS", "").strip()

    if mode == "stdio":
        if not command:
            return None
    elif not url:
        return None

    return build_openclaw_config(
        {
            "url": url,
            "mode": mode or DEFAULT_OPENCLAW_MCP_MODE,
            "command": command,
            "args": [part for part in args_env.split() if part],
            "auth_token": auth_token,
        }
    )


@asynccontextmanager
async def _open_openclaw_session(config: OpenClawConfig) -> AsyncIterator[ClientSession]:
    """Open an MCP client session for OpenClaw using the configured transport."""
    stack = AsyncExitStack()
    try:
        if config.mode == "stdio":
            if not config.command:
                raise ValueError(
                    "Invalid OpenClaw config: mode=stdio requires command "
                    "(set OPENCLAW_MCP_COMMAND or pass command in config)."
                )
            server_params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env={
                    **os.environ,
                    **(
                        {"OPENCLAW_AUTH_TOKEN": config.auth_token}
                        if config.auth_token
                        else {}
                    ),
                },
            )
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(server_params)
            )

        elif config.mode == "sse":
            if not config.url:
                raise ValueError(
                    "Invalid OpenClaw config: mode=sse requires url "
                    "(set OPENCLAW_MCP_URL, e.g. https://.../sse)."
                )
            read_stream, write_stream = await stack.enter_async_context(
                sse_client(
                    config.url,
                    headers=config.request_headers,
                    timeout=config.timeout_seconds,
                    sse_read_timeout=max(60.0, config.timeout_seconds),
                )
            )

        elif config.mode == "streamable-http":
            if not config.url:
                raise ValueError(
                    "Invalid OpenClaw config: mode=streamable-http requires url "
                    "(set OPENCLAW_MCP_URL)."
                )
            http_client = await stack.enter_async_context(
                httpx.AsyncClient(
                    headers=config.request_headers,
                    timeout=config.timeout_seconds,
                )
            )
            read_stream, write_stream, _ = await stack.enter_async_context(
                streamable_http_client(
                    config.url,
                    http_client=http_client,
                    headers=config.request_headers,
                    timeout=config.timeout_seconds,
                    sse_read_timeout=max(60.0, config.timeout_seconds),
                )
            )

        else:
            raise ValueError(
                f"Unsupported OpenClaw MCP mode '{config.mode}'. "
                "Supported modes: stdio, sse, streamable-http."
            )

        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        yield session

    finally:
        await stack.aclose()


def _run_async(coro: Coroutine[object, object, object]) -> object:
    return asyncio.run(coro)


def _tool_result_to_dict(result: types.CallToolResult) -> OpenClawToolCallResult:
    text_parts: list[str] = []
    content_items: list[OpenClawContentItem] = []

    for item in result.content:
        if isinstance(item, types.TextContent):
            text_parts.append(item.text)
            content_items.append({"type": "text", "text": item.text})
        elif isinstance(item, types.EmbeddedResource):
            resource = item.resource
            if isinstance(resource, types.TextResourceContents):
                content_items.append(
                    {
                        "type": "resource_text",
                        "uri": str(resource.uri),
                        "text": resource.text,
                    }
                )
                text_parts.append(resource.text)
            elif isinstance(resource, types.BlobResourceContents):
                content_items.append(
                    {
                        "type": "resource_blob",
                        "uri": str(resource.uri),
                        "mime_type": resource.mimeType or "",
                    }
                )
        else:
            content_items.append({"type": getattr(item, "type", "unknown")})

    structured = getattr(result, "structuredContent", None)
    text_output = "\n".join(part.strip() for part in text_parts if part.strip()).strip()
    return {
        "is_error": bool(result.isError),
        "text": text_output,
        "content": content_items,
        "structured_content": structured,
    }


async def _list_tools_async(config: OpenClawConfig) -> list[types.Tool]:
    async with _open_openclaw_session(config) as session:
        result = await session.list_tools()
        return list(result.tools)


def list_openclaw_tools(config: OpenClawConfig) -> list[OpenClawToolDescriptor]:
    """List available tools from an OpenClaw MCP server."""
    tools = _list_tools_sync(config)
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": getattr(tool, "inputSchema", None),
        }
        for tool in tools
    ]


def _list_tools_sync(config: OpenClawConfig) -> list[types.Tool]:
    return cast(list[types.Tool], _run_async(_list_tools_async(config)))


async def _call_tool_async(
    config: OpenClawConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> OpenClawToolCallResult:
    async with _open_openclaw_session(config) as session:
        result = await session.call_tool(tool_name, arguments or {})
        payload = _tool_result_to_dict(result)
        payload["tool"] = tool_name
        payload["arguments"] = arguments or {}
        return payload


def call_openclaw_tool(
    config: OpenClawConfig,
    tool_name: str,
    arguments: dict[str, object] | None = None,
) -> OpenClawToolCallResult:
    """Call an OpenClaw MCP tool and normalize the result."""
    return cast(OpenClawToolCallResult, _run_async(_call_tool_async(config, tool_name, arguments)))


def validate_openclaw_config(config: OpenClawConfig) -> OpenClawValidationResult:
    """Validate OpenClaw MCP connectivity by listing available tools."""
    if not config.is_configured:
        return OpenClawValidationResult(
            ok=False,
            detail="OpenClaw is not configured: provide a URL (HTTP/SSE) or command (stdio).",
        )

    if config.mode != "stdio" and _is_probable_openclaw_control_ui_url(config.url):
        return OpenClawValidationResult(
            ok=False,
            detail=(
                "OpenClaw MCP validation failed: the local URL on port 18789 is OpenClaw's "
                "Control UI/Gateway, not its MCP bridge. Use mode `stdio` with command "
                f"`{_OPENCLAW_STDIO_COMMAND}` and args `{' '.join(_OPENCLAW_STDIO_ARGS)}`."
            ),
        )

    try:
        tools = list_openclaw_tools(config)
        tool_names = tuple(sorted(t["name"] for t in tools))
        endpoint = config.url if config.mode != "stdio" else config.command
        return OpenClawValidationResult(
            ok=True,
            detail=(
                f"OpenClaw MCP connected via {config.mode} ({endpoint}); "
                f"discovered {len(tool_names)} tool(s)."
            ),
            tool_names=tool_names,
        )
    except Exception as err:  # noqa: BLE001
        return OpenClawValidationResult(
            ok=False,
            detail=f"OpenClaw MCP validation failed: {describe_openclaw_error(err, config)}",
        )
