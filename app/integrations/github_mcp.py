"""Shared GitHub MCP integration helpers.

This module centralizes GitHub MCP configuration, validation, and tool calling
so the onboarding wizard, verify CLI, chat tools, and investigation actions all
use the same transport and parsing logic.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast

import httpx
from mcp import ClientSession, StdioServerParameters, types  # type: ignore[import-not-found]
from mcp.client.sse import sse_client  # type: ignore[import-not-found]
from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]
from pydantic import Field, field_validator, model_validator

from app.integrations.mcp_streamable_http_compat import streamable_http_client
from app.strict_config import StrictConfigModel

DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
DEFAULT_GITHUB_MCP_MODE = "streamable-http"
DEFAULT_GITHUB_MCP_TOOLSETS = ("repos", "issues", "pull_requests", "actions")
REQUIRED_SOURCE_INVESTIGATION_TOOLS = (
    "get_file_contents",
    "get_repository_tree",
    "list_commits",
    "search_code",
)


class GitHubMCPConfig(StrictConfigModel):
    """Normalized GitHub MCP connection settings."""

    url: str = DEFAULT_GITHUB_MCP_URL
    mode: Literal["stdio", "sse", "streamable-http"] = "streamable-http"
    auth_token: str = ""
    command: str = ""
    args: tuple[str, ...] = ()
    headers: dict[str, str] = Field(default_factory=dict)
    toolsets: tuple[str, ...] = DEFAULT_GITHUB_MCP_TOOLSETS
    timeout_seconds: float = Field(default=15.0, gt=0)
    integration_id: str = ""

    @field_validator("url", mode="before")
    @classmethod
    def _normalize_url(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_GITHUB_MCP_URL).strip()
        return normalized or DEFAULT_GITHUB_MCP_URL

    @field_validator("mode", mode="before")
    @classmethod
    def _normalize_mode(cls, value: Any) -> str:
        normalized = str(value or DEFAULT_GITHUB_MCP_MODE).strip().lower()
        return normalized or DEFAULT_GITHUB_MCP_MODE

    @field_validator("args", mode="before")
    @classmethod
    def _normalize_args(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return ()
        return tuple(str(arg).strip() for arg in value if str(arg).strip())

    @field_validator("headers", mode="before")
    @classmethod
    def _normalize_headers(cls, value: Any) -> dict[str, str]:
        if not isinstance(value, dict):
            return {}
        return {str(key): str(item).strip() for key, item in value.items() if str(item).strip()}

    @field_validator("toolsets", mode="before")
    @classmethod
    def _normalize_toolsets(cls, value: Any) -> tuple[str, ...]:
        if value is None:
            return DEFAULT_GITHUB_MCP_TOOLSETS
        toolsets = tuple(str(toolset).strip() for toolset in value if str(toolset).strip())
        return toolsets or DEFAULT_GITHUB_MCP_TOOLSETS

    @model_validator(mode="after")
    def _validate_transport_requirements(self) -> GitHubMCPConfig:
        if self.mode == "stdio" and not self.command:
            raise ValueError("GitHub MCP mode 'stdio' requires a non-empty command.")
        if self.mode != "stdio" and not self.url:
            raise ValueError(f"GitHub MCP mode '{self.mode}' requires a non-empty url.")
        return self

    @property
    def request_headers(self) -> dict[str, str]:
        headers = {key: value for key, value in self.headers.items() if value}
        if self.auth_token and "Authorization" not in headers:
            headers["Authorization"] = f"Bearer {self.auth_token}"
        return headers


@dataclass(frozen=True)
class GitHubMCPValidationResult:
    """Result of validating a GitHub MCP connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()
    authenticated_user: str = ""


def build_github_mcp_config(raw: dict[str, Any] | None) -> GitHubMCPConfig:
    """Build a normalized config object from env/store data."""
    return GitHubMCPConfig.model_validate(raw or {})


def github_mcp_config_from_env() -> GitHubMCPConfig | None:
    """Load a GitHub MCP config from env vars."""
    mode = os.getenv("GITHUB_MCP_MODE", DEFAULT_GITHUB_MCP_MODE).strip().lower()
    url = os.getenv("GITHUB_MCP_URL", "").strip()
    command = os.getenv("GITHUB_MCP_COMMAND", "").strip()
    auth_token = os.getenv("GITHUB_MCP_AUTH_TOKEN", "").strip()
    toolsets_env = os.getenv("GITHUB_MCP_TOOLSETS", "").strip()
    args_env = os.getenv("GITHUB_MCP_ARGS", "").strip()

    if mode == "stdio":
        if not command:
            return None
    elif not url:
        return None

    return build_github_mcp_config({
        "url": url or DEFAULT_GITHUB_MCP_URL,
        "mode": mode or DEFAULT_GITHUB_MCP_MODE,
        "command": command,
        "args": [part for part in args_env.split() if part],
        "auth_token": auth_token,
        "toolsets": [part.strip() for part in toolsets_env.split(",") if part.strip()],
    })


@asynccontextmanager
async def _open_github_mcp_session(config: GitHubMCPConfig) -> AsyncIterator[ClientSession]:
    stack = AsyncExitStack()
    try:
        if config.mode == "stdio":
            if not config.command:
                raise ValueError(
                    "Invalid GitHub MCP config: mode=stdio requires command "
                    "(set OPENSRE_GITHUB_MCP_COMMAND or pass command "
                    "in config)."
                )
            server_params = StdioServerParameters(
                command=config.command,
                args=list(config.args),
                env={
                    **os.environ,
                    **({"GITHUB_PERSONAL_ACCESS_TOKEN": config.auth_token} if config.auth_token else {}),
                },
            )
            read_stream, write_stream = await stack.enter_async_context(stdio_client(server_params))
        elif config.mode == "sse":
            if not config.url:
                raise ValueError(
                    "Invalid GitHub MCP config: mode=sse requires url "
                    "(set OPENSRE_GITHUB_MCP_URL, "
                    "for example https://.../sse)."
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
                    "Invalid GitHub MCP config: "
                    "mode=streamable-http requires url "
                    "(set OPENSRE_GITHUB_MCP_URL)."
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
                f"Unsupported GitHub MCP mode '{config.mode}'. "
                "Supported modes: stdio, sse, streamable-http."
            )

        session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
        await session.initialize()
        yield session
    finally:
        await stack.aclose()


def _run_async(coro: Any) -> Any:
    return asyncio.run(coro)


def _tool_result_to_dict(result: types.CallToolResult) -> dict[str, Any]:
    text_parts: list[str] = []
    content_items: list[dict[str, Any]] = []

    for item in result.content:
        if isinstance(item, types.TextContent):
            text_parts.append(item.text)
            content_items.append({"type": "text", "text": item.text})
        elif isinstance(item, types.EmbeddedResource):
            resource = item.resource
            if isinstance(resource, types.TextResourceContents):
                content_items.append({
                    "type": "resource_text",
                    "uri": str(resource.uri),
                    "text": resource.text,
                })
                text_parts.append(resource.text)
            elif isinstance(resource, types.BlobResourceContents):
                content_items.append({
                    "type": "resource_blob",
                    "uri": str(resource.uri),
                    "mime_type": resource.mimeType,
                })
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


async def _list_tools_async(config: GitHubMCPConfig) -> list[types.Tool]:
    async with _open_github_mcp_session(config) as session:
        result = await session.list_tools()
        return list(result.tools)


def list_github_mcp_tools(config: GitHubMCPConfig) -> list[dict[str, Any]]:
    """List available tools from a GitHub MCP server."""

    tools = _run_async(_list_tools_async(config))
    return [
        {
            "name": tool.name,
            "description": tool.description or "",
            "input_schema": getattr(tool, "inputSchema", None),
        }
        for tool in tools
    ]


async def _call_tool_async(
    config: GitHubMCPConfig,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    async with _open_github_mcp_session(config) as session:
        result = await session.call_tool(tool_name, arguments or {})
        payload = _tool_result_to_dict(result)
        payload["tool"] = tool_name
        payload["arguments"] = arguments or {}
        return payload


def call_github_mcp_tool(
    config: GitHubMCPConfig,
    tool_name: str,
    arguments: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Call a GitHub MCP tool and normalize the result."""

    return cast(dict[str, Any], _run_async(_call_tool_async(config, tool_name, arguments)))


def validate_github_mcp_config(config: GitHubMCPConfig) -> GitHubMCPValidationResult:
    """Validate connectivity, authentication, and source-code tools."""

    try:
        tools = list_github_mcp_tools(config)
        tool_names = tuple(sorted(tool["name"] for tool in tools))
        missing = sorted(set(REQUIRED_SOURCE_INVESTIGATION_TOOLS) - set(tool_names))
        if missing:
            return GitHubMCPValidationResult(
                ok=False,
                detail=(
                    "GitHub MCP connected, but required repository investigation tools are missing: "
                    f"{', '.join(missing)}."
                ),
                tool_names=tool_names,
            )

        me_result = call_github_mcp_tool(config, "get_me", {})
        if me_result.get("is_error"):
            detail = me_result.get("text") or "Unknown authentication failure."
            return GitHubMCPValidationResult(
                ok=False,
                detail=f"GitHub MCP connected, but authentication failed: {detail}",
                tool_names=tool_names,
            )

        structured = me_result.get("structured_content") or {}
        user_name = str(structured.get("login") or structured.get("name") or "").strip()
        if not user_name:
            try:
                payload = json.loads(me_result.get("text", "{}"))
                user_name = str(payload.get("login") or payload.get("name") or "").strip()
            except json.JSONDecodeError:
                user_name = ""

        who = user_name or "authenticated GitHub user"
        return GitHubMCPValidationResult(
            ok=True,
            detail=(
                f"GitHub MCP validated for {who}; discovered {len(tool_names)} tools including "
                "repository source investigation helpers."
            ),
            tool_names=tool_names,
            authenticated_user=user_name,
        )
    except Exception as err:  # noqa: BLE001
        return GitHubMCPValidationResult(ok=False, detail=f"GitHub MCP validation failed: {err}")


def build_github_code_search_query(owner: str, repo: str, query: str) -> str:
    """Build a repo-scoped GitHub code search query."""

    repo_qualifier = f"repo:{owner}/{repo}"
    query = query.strip()
    if repo_qualifier in query:
        return query
    return f"{query} {repo_qualifier}".strip()
