"""Shared GitHub MCP integration helpers.

This module centralizes GitHub MCP configuration, validation, and tool calling
so the onboarding wizard, verify CLI, chat tools, and investigation actions all
use the same transport and parsing logic.
"""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator, Sequence
from contextlib import AsyncExitStack, asynccontextmanager
from dataclasses import dataclass
from typing import Any, Literal, cast
from urllib.parse import urlparse, urlunparse

import httpx
from mcp import ClientSession, StdioServerParameters, types  # type: ignore[import-not-found]
from mcp.client.sse import sse_client  # type: ignore[import-not-found]
from mcp.client.stdio import stdio_client  # type: ignore[import-not-found]
from pydantic import Field, field_validator, model_validator
from rich.console import Console, Group
from rich.panel import Panel
from rich.table import Table

from app.integrations.mcp_streamable_http_compat import streamable_http_client
from app.strict_config import StrictConfigModel

DEFAULT_GITHUB_MCP_URL = "https://api.githubcopilot.com/mcp/"
DEFAULT_GITHUB_MCP_MODE = "streamable-http"
DEFAULT_GITHUB_MCP_TOOLSETS = ("repos", "issues", "pull_requests", "actions", "search")

REQUIRED_SOURCE_INVESTIGATION_TOOLS = (
    "get_file_contents",
    "get_repository_tree",
    "list_commits",
    "search_code",
)

# Prefer tools that list repos with no args; hosted Copilot MCP often omits list_repositories.
_REPO_PROBE_NO_ARG_TOOLS: tuple[str, ...] = (
    "list_repositories",
    "list_user_repositories",
    "list_starred_repositories",
)

# Default cap on repos captured from one MCP list/search call (display + verify).
# Keeps responses and terminal output bounded; not a GitHub API limit. Override with
# OPENSRE_GITHUB_MCP_REPO_PROBE_LIMIT (integer, clamped 5..500).
_DEFAULT_REPO_PROBE_LIMIT = 50

_GITHUB_MCP_DISPLAY_LEVELS = frozenset({"summary", "standard", "full"})
GitHubMcpDisplayDetailLevel = Literal["summary", "standard", "full"]
GitHubMcpRepoView = Literal["auto", "user", "accessible", "starred", "search_user"]
GitHubMcpRepoVisibilityFilter = Literal["any", "public", "private"]


def _is_github_copilot_generic_mcp_root(url: str) -> bool:
    """True when ``url`` is the default Copilot MCP root (``.../mcp`` or ``.../mcp/``).

    That root is rewritten to ``/mcp/x/all/readonly`` for sessions. For request headers,
    we must not also send ``X-MCP-Toolsets`` with a subset, or the server narrows tools
    below the read-only surface (e.g. omits ``search_repositories``).
    """

    raw = url.strip()
    if not raw:
        return False
    parsed = urlparse(raw)
    host = parsed.netloc.lower()
    if "@" in host:
        host = host.split("@", 1)[-1]
    if host != "api.githubcopilot.com":
        return False
    path_norm = (parsed.path or "/").rstrip("/").lower() or "/"
    return path_norm == "/mcp"


def _remote_github_mcp_session_url(url: str) -> str:
    """Map generic Copilot MCP base URL to a path that exposes full read-only tools.

    ``https://api.githubcopilot.com/mcp/`` negotiates a *default* tool surface that is
    smaller than the ``repos`` toolset (often omitting ``get_repository_tree``).
    GitHub documents path-based selection: ``/mcp/x/all/readonly`` enables every
    read-only tool. See github-mcp-server ``docs/remote-server.md``.

    Custom paths (e.g. ``/mcp/x/repos``, ``/mcp/readonly``) are left unchanged.
    """

    raw = url.strip()
    if not raw:
        return raw
    if not _is_github_copilot_generic_mcp_root(url):
        return raw
    parsed = urlparse(raw)
    new_path = "/mcp/x/all/readonly"
    return urlunparse(
        (parsed.scheme, parsed.netloc, new_path, parsed.params, parsed.query, parsed.fragment)
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
        # Remote Copilot: explicit paths (e.g. ``/mcp/x/issues``) may need ``X-MCP-Toolsets``
        # to merge toolsets. The generic ``/mcp`` root is rewritten to ``/mcp/x/all/readonly``
        # for the session; do not send a subset via ``X-MCP-Toolsets`` there or tools like
        # ``search_repositories`` disappear from the catalog.
        if (
            self.mode != "stdio"
            and self.toolsets
            and "X-MCP-Toolsets" not in headers
            and not _is_github_copilot_generic_mcp_root(self.url)
        ):
            headers["X-MCP-Toolsets"] = ",".join(self.toolsets)
        return headers


@dataclass(frozen=True)
class GitHubMCPRepoProbeRow:
    """One repository row parsed from an MCP list/search payload (best-effort metadata)."""

    full_name: str
    private: bool | None
    fork: bool | None


@dataclass(frozen=True)
class GitHubMCPValidationResult:
    """Result of validating a GitHub MCP connection."""

    ok: bool
    detail: str
    tool_names: tuple[str, ...] = ()
    authenticated_user: str = ""
    failure_category: str = ""
    repo_access_count: int | None = None
    repo_access_scope_owners: tuple[str, ...] = ()
    repo_access_samples: tuple[str, ...] = ()
    repo_access_probe_tool: str = ""
    repo_access_probe_rows: tuple[GitHubMCPRepoProbeRow, ...] = ()
    repo_access_probe_limit_applied: int = 0
    profile_public_repos: int | None = None
    profile_private_repos: int | None = None


_FAILURE_TYPE_LABELS: dict[str, str] = {
    "connectivity": "connectivity or transport (could not reach or initialize the MCP server)",
    "authentication": "authentication (token missing, invalid, or rejected by GitHub)",
    "insufficient_tools": "toolset (required MCP tools are not exposed; widen toolsets or server config)",
    "repository_access": "repository access (repo listing failed or token lacks repo API access)",
}


def print_github_mcp_validation_report(
    result: GitHubMCPValidationResult,
    *,
    console: Console | None = None,
    detail_level: GitHubMcpDisplayDetailLevel = "standard",
) -> None:
    """Print validation outcome with Rich (tables / panels) for setup and wizard."""

    if detail_level not in _GITHUB_MCP_DISPLAY_LEVELS:
        detail_level = "standard"
    out = console if console is not None else Console(highlight=False, soft_wrap=True)

    if not result.ok:
        body = format_github_mcp_validation_cli_report(result)
        out.print(
            Panel.fit(
                body.strip(),
                title="[bold red]GitHub MCP · validation failed[/]",
                border_style="red",
            )
        )
        return

    who = (result.authenticated_user or "").strip()
    identity = f"@{who}" if who else "(authenticated; login not in response)"
    count = result.repo_access_count
    count_str = "—" if count is None else str(count)
    samples = list(result.repo_access_samples)
    profile_pub = result.profile_public_repos
    profile_priv = result.profile_private_repos
    profile_counts_only = bool(count is not None and count > 0 and not samples)

    summary = Table.grid(padding=(0, 2))
    summary.add_column(style="dim", justify="right")
    summary.add_column()
    summary.add_row("Status", "[green]Configuration validation: succeeded[/]")
    summary.add_row("GitHub identity", identity)
    summary.add_row("Repositories returned (probe)", count_str)
    if profile_pub is not None or profile_priv is not None:
        pub_s = "—" if profile_pub is None else str(profile_pub)
        priv_s = "—" if profile_priv is None else str(profile_priv)
        summary.add_row("Profile totals", f"public {pub_s}, private {priv_s}")

    if profile_counts_only:
        summary.add_row(
            "",
            "[dim]Counts from GitHub profile (no sample repo names in this session).[/]",
        )

    blocks: list[Any] = [summary]

    # "summary" is intentionally minimal and avoids printing repo names.
    if detail_level in {"standard", "full"}:
        scope = (
            ", ".join(result.repo_access_scope_owners) if result.repo_access_scope_owners else "—"
        )
        summary.add_row("Owners in scope", scope)

    probe_tool = (result.repo_access_probe_tool or "").strip()
    if detail_level in {"standard", "full"} and probe_tool:
        summary.add_row(
            "Access source",
            f"[bold]{_probe_source_label(probe_tool)}[/]\n[dim]{_probe_listing_caption(probe_tool)}[/]",
        )

    rows_detail = list(result.repo_access_probe_rows)
    starred_col = _probe_starred_list_column(probe_tool)

    # Only show repo names in the expanded view; "standard" stays clean.
    if detail_level == "full" and (rows_detail or samples):
        repo_table = Table(
            title="[bold]Repositories[/]",
            show_header=True,
            header_style="bold dim",
            border_style="dim",
        )
        repo_table.add_column("#", justify="right", style="dim", width=4)
        if rows_detail:
            repo_table.add_column("Repository", style="default")
            repo_table.add_column("Visibility", justify="center")
            repo_table.add_column("Fork", justify="center")
            repo_table.add_column("Starred (this list)", justify="center", style="dim")
            for i, row in enumerate(rows_detail, start=1):
                repo_table.add_row(
                    str(i),
                    row.full_name,
                    _visibility_cell(row.private),
                    _fork_cell(row.fork),
                    starred_col,
                )
        else:
            repo_table.add_column("owner/repo", style="default")
            for i, name in enumerate(samples, start=1):
                repo_table.add_row(str(i), name)
        blocks.append(repo_table)
    elif detail_level == "full" and not samples and not profile_counts_only:
        blocks.append("[dim]No repository names were returned by the listing probe.[/]")

    panel_body: Any = Group(*blocks) if len(blocks) > 1 else blocks[0]
    out.print(
        Panel.fit(
            panel_body,
            title="[bold green]GitHub MCP · connected[/]",
            border_style="green",
        )
    )


def format_github_mcp_validation_cli_report(result: GitHubMCPValidationResult) -> str:
    """Multi-line human report for verify output and non-rich contexts."""

    if not result.ok:
        category = (result.failure_category or "unknown").strip() or "unknown"
        label = _FAILURE_TYPE_LABELS.get(category, category)
        return "\n".join(
            [
                "Configuration validation: failed",
                f"Failure type: {label}",
                f"Details: {result.detail}",
            ]
        )

    who = (result.authenticated_user or "").strip()
    identity_line = (
        f"GitHub identity: @{who}"
        if who
        else "GitHub identity: (authenticated; login not in response)"
    )
    n = 0 if result.repo_access_count is None else result.repo_access_count
    scope_txt = (
        ", ".join(result.repo_access_scope_owners)
        if result.repo_access_scope_owners
        else "none identified from listing"
    )
    samples_txt = (
        ", ".join(result.repo_access_samples)
        if result.repo_access_samples
        else "none in parsed listing"
    )
    lines = [
        "Configuration validation: succeeded",
        identity_line,
        f"Repositories returned (probe): {n}",
        f"Organization and user scope (owners in listing): {scope_txt}",
        f"Representative repositories: {samples_txt}",
    ]
    pub_n = result.profile_public_repos
    priv_n = result.profile_private_repos
    if pub_n is not None or priv_n is not None:
        pub_s = "—" if pub_n is None else str(pub_n)
        priv_s = "—" if priv_n is None else str(priv_n)
        lines.append(f"Profile totals: public {pub_s}, private {priv_s}")

    probe_tool = (result.repo_access_probe_tool or "").strip()
    if probe_tool:
        lines.append(
            f"Repository access source: {_probe_source_label(probe_tool)} — "
            f"{_probe_listing_caption(probe_tool)}"
        )
    agg = _repo_probe_aggregate_hint(result.repo_access_probe_rows)
    if agg:
        lines.append(f"Sample fields from API (partial): {agg}")
    if result.repo_access_probe_limit_applied:
        lines.append(
            f"Stored up to {result.repo_access_probe_limit_applied} repos from this response "
            "(set OPENSRE_GITHUB_MCP_REPO_PROBE_LIMIT to change the cap)."
        )
    return "\n".join(lines)


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

    return build_github_mcp_config(
        {
            "url": url or DEFAULT_GITHUB_MCP_URL,
            "mode": mode or DEFAULT_GITHUB_MCP_MODE,
            "command": command,
            "args": [part for part in args_env.split() if part],
            "auth_token": auth_token,
            "toolsets": [part.strip() for part in toolsets_env.split(",") if part.strip()],
        }
    )


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
                    **(
                        {"GITHUB_PERSONAL_ACCESS_TOKEN": config.auth_token}
                        if config.auth_token
                        else {}
                    ),
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
            session_url = _remote_github_mcp_session_url(config.url)
            read_stream, write_stream = await stack.enter_async_context(
                sse_client(
                    session_url,
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
            session_url = _remote_github_mcp_session_url(config.url)
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


def _root_cause_message(exc: BaseException) -> str:
    """Best-effort unwrap for ExceptionGroup/TaskGroup."""

    if isinstance(exc, ExceptionGroup) and exc.exceptions:
        return _root_cause_message(exc.exceptions[0])
    cause = getattr(exc, "__cause__", None)
    if isinstance(cause, BaseException):
        return _root_cause_message(cause)
    context = getattr(exc, "__context__", None)
    if isinstance(context, BaseException):
        return _root_cause_message(context)
    return f"{exc.__class__.__name__}: {exc}"


def _connectivity_failure_detail(err: BaseException) -> str:
    msg = _root_cause_message(err)
    return "\n".join(
        [
            msg,
            "",
            "Check: outbound HTTPS",
            "- to api.githubcopilot.com",
            "- token validity / GitHub auth session",
            "- toolsets and MCP base URL path",
        ]
    ).strip()


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
                        "mime_type": resource.mimeType,
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


def _json_schema_allows_empty_object_call(schema: Any) -> bool:
    """True if the tool can be invoked with {} (no required properties)."""

    if schema is None:
        return True
    if not isinstance(schema, dict):
        return False
    if schema.get("allOf") or schema.get("anyOf") or schema.get("oneOf"):
        return False
    required = schema.get("required")
    return not (isinstance(required, list) and len(required) > 0)


def _owners_from_repo_full_names(names: Sequence[str]) -> tuple[str, ...]:
    owners: list[str] = []
    seen: set[str] = set()
    for full in names:
        if "/" not in full:
            continue
        owner = full.split("/", 1)[0].strip()
        if owner and owner not in seen:
            seen.add(owner)
            owners.append(owner)
    return tuple(sorted(owners, key=str.lower))


def _repo_probe_capture_limit() -> int:
    raw = os.getenv("OPENSRE_GITHUB_MCP_REPO_PROBE_LIMIT", "").strip()
    if not raw:
        return _DEFAULT_REPO_PROBE_LIMIT
    try:
        return max(5, min(int(raw), 500))
    except ValueError:
        return _DEFAULT_REPO_PROBE_LIMIT


def _probe_listing_caption(tool_name: str) -> str:
    """Explain what the probe list represents (depends on which MCP tool ran)."""

    t = (tool_name or "").strip().lower()
    if t == "list_starred_repositories":
        return "Repositories you have starred (not necessarily owned by you)."
    if t == "list_user_repositories":
        return "Repositories for your user account as defined by the GitHub MCP tool."
    if t == "list_repositories":
        return "Repositories returned for this token (tool-defined scope)."
    if t == "search_repositories":
        return "Repositories matching the probe search query (e.g. user:yourlogin)."
    return "Repositories returned by the MCP probe."


def _probe_source_label(tool_name: str) -> str:
    """Human-friendly label for which repo list we displayed."""

    t = (tool_name or "").strip().lower()
    if t == "list_starred_repositories":
        return "Starred repositories"
    if t == "list_user_repositories":
        return "User repositories"
    if t == "list_repositories":
        return "Accessible repositories"
    if t == "search_repositories":
        return "Repository search results"
    return "Repository listing"


def _probe_starred_list_column(tool_name: str) -> str:
    """Whether every row in this listing is a starred repo (by probe semantics)."""

    t = (tool_name or "").strip().lower()
    if t == "list_starred_repositories":
        return "yes"
    return "—"


def _visibility_cell(private: bool | None) -> str:
    if private is True:
        return "private"
    if private is False:
        return "public"
    return "unknown"


def _fork_cell(fork: bool | None) -> str:
    if fork is True:
        return "yes"
    if fork is False:
        return "no"
    return "unknown"


def _repo_visibility_counts_from_get_me_profile(
    structured: dict[str, Any],
    text: str,
) -> tuple[int | None, int | None]:
    def extract(obj: dict[str, Any]) -> tuple[int | None, int | None]:
        details = obj.get("details") or obj.get("Details")
        if not isinstance(details, dict):
            return None, None
        pub = details.get("public_repos") or details.get("publicRepos")
        priv = details.get("total_private_repos") or details.get("totalPrivateRepos")
        try:
            public_n = int(pub) if pub is not None else None
        except (TypeError, ValueError):
            public_n = None
        try:
            private_n = int(priv) if priv is not None else None
        except (TypeError, ValueError):
            private_n = None
        return public_n, private_n

    got = extract(structured)
    if got != (None, None):
        return got
    try:
        payload = json.loads(text or "{}")
    except json.JSONDecodeError:
        return None, None
    return extract(payload) if isinstance(payload, dict) else (None, None)


def _repo_counts_from_get_me_profile(structured: dict[str, Any], text: str) -> int | None:
    """Best-effort total repo count from get_me (public + private) when listing tools are absent."""

    pub, priv = _repo_visibility_counts_from_get_me_profile(structured, text)
    if pub is None and priv is None:
        return None
    return int(pub or 0) + int(priv or 0)


def _repo_probe_aggregate_hint(rows: Sequence[GitHubMCPRepoProbeRow]) -> str | None:
    if not rows:
        return None
    public_n = sum(1 for r in rows if r.private is False)
    private_n = sum(1 for r in rows if r.private is True)
    unknown_vis = sum(1 for r in rows if r.private is None)
    fork_n = sum(1 for r in rows if r.fork is True)
    parts: list[str] = []
    if public_n or private_n or unknown_vis:
        parts.append(
            f"visibility in sample: public {public_n}, private {private_n}, unknown {unknown_vis}"
        )
    if fork_n:
        parts.append(f"forks in sample: {fork_n}")
    return "; ".join(parts) if parts else None


def _filter_repo_rows(
    rows: Sequence[GitHubMCPRepoProbeRow],
    *,
    visibility: GitHubMcpRepoVisibilityFilter = "any",
) -> list[GitHubMCPRepoProbeRow]:
    if visibility == "any":
        return list(rows)
    want_private = visibility == "private"
    return [r for r in rows if r.private is not None and r.private is want_private]


def _repo_dict_to_row(node: dict[str, Any]) -> GitHubMCPRepoProbeRow | None:
    fn = node.get("full_name") or node.get("fullName")
    full: str | None = None
    if isinstance(fn, str) and fn.strip():
        full = fn.strip()
    else:
        owner = node.get("owner")
        name = node.get("name")
        if isinstance(owner, dict) and isinstance(name, str):
            login = owner.get("login")
            if isinstance(login, str) and login.strip():
                full = f"{login.strip()}/{name.strip()}"
    if not full:
        return None
    priv_raw = node.get("private")
    vis = node.get("visibility")
    private: bool | None
    if isinstance(priv_raw, bool):
        private = priv_raw
    elif isinstance(vis, str):
        v = vis.lower()
        private = True if v == "private" else False if v == "public" else None
    else:
        private = None
    frk = node.get("fork")
    fork = frk if isinstance(frk, bool) else None
    return GitHubMCPRepoProbeRow(full_name=full, private=private, fork=fork)


def _collect_repo_probe_rows_from_payload(data: Any) -> list[GitHubMCPRepoProbeRow]:
    found: list[GitHubMCPRepoProbeRow] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, list):
            for item in node:
                walk(item)
            return
        if isinstance(node, dict):
            row = _repo_dict_to_row(node)
            if row and row.full_name not in seen:
                seen.add(row.full_name)
                found.append(row)
            for _key, val in node.items():
                if isinstance(val, (list, dict)):
                    walk(val)

    walk(data)
    return found


def _repo_probe_rows_from_tool_result(result: dict[str, Any]) -> list[GitHubMCPRepoProbeRow]:
    structured = result.get("structured_content")
    if structured is not None:
        rows = _collect_repo_probe_rows_from_payload(structured)
        if rows:
            return rows
    text = str(result.get("text") or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        return []
    return _collect_repo_probe_rows_from_payload(parsed)


def _repo_probe_order_and_search_fallback(
    view: GitHubMcpRepoView,
) -> tuple[tuple[str, ...], bool]:
    view_norm = view
    if view_norm == "starred":
        return ("list_starred_repositories",), False
    if view_norm == "user":
        return ("list_user_repositories",), True
    if view_norm == "accessible":
        return ("list_repositories",), False
    if view_norm == "search_user":
        return (), True
    return _REPO_PROBE_NO_ARG_TOOLS, True


def _repo_probe_attempts(
    tools: list[dict[str, Any]],
    authenticated_login: str,
    *,
    view: GitHubMcpRepoView = "auto",
) -> tuple[str, ...]:
    by_name = {str(t["name"]): t for t in tools if t.get("name")}
    ordered, allow_search_fallback = _repo_probe_order_and_search_fallback(view)
    attempts = tuple(name for name in ordered if name in by_name)
    login = (authenticated_login or "").strip()
    if allow_search_fallback and login and "search_repositories" in by_name:
        return (*attempts, "search_repositories")
    return attempts


def _format_repo_probe_attempts(attempts: Sequence[str]) -> str:
    if not attempts:
        return "none"
    if len(attempts) == 1:
        return attempts[0]
    return f"{', '.join(attempts[:-1])}, then {attempts[-1]}"


def _plan_repo_access_probe(
    tools: list[dict[str, Any]],
    authenticated_login: str,
    *,
    view: GitHubMcpRepoView = "auto",
) -> tuple[str, dict[str, Any]] | None:
    """Pick a tool + arguments to sample repo access (hosted MCP shapes differ from local)."""

    by_name = {str(t["name"]): t for t in tools if t.get("name")}
    ordered, allow_search_fallback = _repo_probe_order_and_search_fallback(view)

    for name in ordered:
        entry = by_name.get(name)
        if not entry:
            continue
        if _json_schema_allows_empty_object_call(entry.get("input_schema")):
            return name, {}
    login = (authenticated_login or "").strip()
    if allow_search_fallback and login and "search_repositories" in by_name:
        return "search_repositories", {"query": f"user:{login}"}
    return None


def validate_github_mcp_config(
    config: GitHubMCPConfig,
    *,
    repo_view: GitHubMcpRepoView = "auto",
    repo_visibility: GitHubMcpRepoVisibilityFilter = "any",
) -> GitHubMCPValidationResult:
    """Validate connectivity, authentication, and repo-access readiness."""

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
                failure_category="insufficient_tools",
            )

        if "get_me" not in tool_names:
            return GitHubMCPValidationResult(
                ok=False,
                detail=(
                    "GitHub MCP connected, but the required identity tool 'get_me' is not exposed. "
                    "Widen your toolsets to include it."
                ),
                tool_names=tool_names,
                failure_category="insufficient_tools",
            )

        me_result = call_github_mcp_tool(config, "get_me", {})
        if me_result.get("is_error"):
            detail = me_result.get("text") or "Unknown authentication failure."
            return GitHubMCPValidationResult(
                ok=False,
                detail=f"GitHub MCP connected, but authentication failed: {detail}",
                tool_names=tool_names,
                failure_category="authentication",
            )

        structured: dict[str, Any] = {}
        raw_structured = me_result.get("structured_content")
        if isinstance(raw_structured, dict):
            structured = raw_structured
        profile_pub, profile_priv = _repo_visibility_counts_from_get_me_profile(
            structured, me_result.get("text", "")
        )
        user_name = str(structured.get("login") or structured.get("name") or "").strip()
        if not user_name:
            try:
                payload = json.loads(me_result.get("text", "{}"))
                user_name = str(payload.get("login") or payload.get("name") or "").strip()
            except json.JSONDecodeError:
                user_name = ""

        who = user_name or "authenticated GitHub user"
        plan = _plan_repo_access_probe(tools, user_name, view=repo_view)
        if plan is None:
            attempted_tools = _repo_probe_attempts(tools, user_name, view=repo_view)
            profile_count = _repo_counts_from_get_me_profile(structured, me_result.get("text", ""))
            if profile_count is not None:
                scope_me = (user_name,) if user_name else ()
                success_detail = (
                    f"OK @{user_name or 'unknown'}; repos={profile_count}; owners={','.join(scope_me) if scope_me else '-'}; "
                    f"examples=-; mcp_tools={len(tool_names)} | repository counts from get_me profile "
                    "(no list/search repo tool exposed)"
                )
                return GitHubMCPValidationResult(
                    ok=True,
                    detail=success_detail,
                    tool_names=tool_names,
                    authenticated_user=user_name,
                    repo_access_count=profile_count,
                    repo_access_scope_owners=scope_me,
                    repo_access_samples=(),
                    profile_public_repos=profile_pub,
                    profile_private_repos=profile_priv,
                )
            return GitHubMCPValidationResult(
                ok=False,
                detail=(
                    f"Authenticated as {who}, but no repository listing or search tool was usable "
                    f"(tried: {_format_repo_probe_attempts(attempted_tools)}). "
                    "Enable toolsets that include repo listing or search (e.g. add `search` or "
                    "`stargazers` alongside repos) for api.githubcopilot.com."
                ),
                tool_names=tool_names,
                authenticated_user=user_name,
                failure_category="repository_access",
                profile_public_repos=profile_pub,
                profile_private_repos=profile_priv,
            )

        repo_tool, repo_args = plan
        list_result = call_github_mcp_tool(config, repo_tool, repo_args)
        if list_result.get("is_error"):
            list_detail = list_result.get("text") or "Unknown error listing repositories."
            return GitHubMCPValidationResult(
                ok=False,
                detail=(
                    f"Authenticated as {who}, but repository access check failed ({repo_tool}): "
                    f"{list_detail} "
                    "(connectivity OK; auth or token scope may be insufficient for repo APIs)."
                ),
                tool_names=tool_names,
                authenticated_user=user_name,
                failure_category="repository_access",
                profile_public_repos=profile_pub,
                profile_private_repos=profile_priv,
            )

        limit = _repo_probe_capture_limit()
        rows_all = _repo_probe_rows_from_tool_result(list_result)
        rows_filtered = _filter_repo_rows(rows_all, visibility=repo_visibility)
        all_names = [r.full_name for r in rows_filtered]
        repo_count = len(all_names)
        samples_rows = tuple(rows_filtered[:limit])
        samples = tuple(r.full_name for r in samples_rows)
        scope = _owners_from_repo_full_names(all_names)
        suffix = ""
        if not all_names:
            suffix = (
                f" | listing had no parseable repos "
                f"({repo_tool} empty or unexpected response shape)"
            )
        success_detail = (
            f"OK @{user_name or 'unknown'}; repos={repo_count}; owners={','.join(scope) if scope else '-'}; "
            f"examples={','.join(samples[:3]) if samples else '-'}; mcp_tools={len(tool_names)}"
            f"{suffix}"
        )

        return GitHubMCPValidationResult(
            ok=True,
            detail=success_detail,
            tool_names=tool_names,
            authenticated_user=user_name,
            repo_access_count=repo_count,
            repo_access_scope_owners=scope,
            repo_access_samples=samples,
            repo_access_probe_tool=repo_tool,
            repo_access_probe_rows=samples_rows,
            repo_access_probe_limit_applied=limit,
            profile_public_repos=profile_pub,
            profile_private_repos=profile_priv,
        )
    except Exception as err:  # noqa: BLE001
        return GitHubMCPValidationResult(
            ok=False,
            detail=_connectivity_failure_detail(err),
            failure_category="connectivity",
        )


def build_github_code_search_query(owner: str, repo: str, query: str) -> str:
    """Build a repo-scoped GitHub code search query."""

    repo_qualifier = f"repo:{owner}/{repo}"
    query = query.strip()
    if repo_qualifier in query:
        return query
    return f"{query} {repo_qualifier}".strip()
