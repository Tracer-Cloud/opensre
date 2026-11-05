"""Shared helpers for the Gitee MCP agent tools.

The discovery scanner skips modules whose name starts with ``_``, so this file
is importable by both tool packages without registering a tool of its own.
"""

from __future__ import annotations

import base64
import contextlib
import json
from typing import Any

from core.domain.types.evidence import record_evidence_entry, unique_evidence_source
from core.tool import report_run_error
from core.tool_framework.utils import first_list, first_string, unavailable_response
from integrations.gitee import (
    DEFAULT_GITEE_MCP_MODE,
    DEFAULT_GITEE_READ_TOOLS,
    GiteeMCPConfig,
    build_gitee_mcp_config,
    call_gitee_mcp_tool,
    describe_gitee_mcp_error,
    gitee_mcp_config_from_env,
    gitee_mcp_runtime_unavailable_reason,
    normalize_gitee_tool_result,
)

COMPONENT = "integrations.gitee.tools._shared"

#: Hidden connection parameters injected by ``extract_params``. Keeping them in
#: every schema lets the tool decorator round-trip stored credentials.
AUTH_PROPERTIES: dict[str, dict[str, object]] = {
    "gitee_url": {"type": "string"},
    "gitee_mode": {"type": "string"},
    "gitee_token": {"type": "string"},
    "gitee_command": {"type": "string"},
    "gitee_args": {"type": "array", "items": {"type": "string"}},
}


def available(sources: dict[str, dict]) -> bool:
    """Whether the verified Gitee MCP connection is present for this turn."""
    return bool(sources.get("gitee_mcp", {}).get("connection_verified"))


def extract_params(sources: dict[str, dict]) -> dict[str, object]:
    """Inject stored Gitee MCP connection settings into a tool call."""
    source = sources.get("gitee_mcp", {})
    if not source:
        return {}
    return {
        "gitee_url": first_string(source, "gitee_url", "url"),
        "gitee_mode": first_string(source, "gitee_mode", "mode"),
        "gitee_token": first_string(source, "gitee_token", "auth_token"),
        "gitee_command": first_string(source, "gitee_command", "command"),
        "gitee_args": first_list(source, "gitee_args", "args"),
    }


def resolve_config(
    url: str | None = None,
    mode: str | None = None,
    token: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
) -> GiteeMCPConfig | None:
    """Resolve a config from explicit tool arguments or the environment."""
    env_config = gitee_mcp_config_from_env()
    if not any((url, mode, token, command, args)):
        return env_config
    inferred_mode = (
        mode
        or ("stdio" if command else "")
        or ("streamable-http" if url else "")
        or (str(env_config.mode) if env_config else "")
        or str(DEFAULT_GITEE_MCP_MODE)
    )
    return build_gitee_mcp_config(
        {
            "url": url or (env_config.url if env_config else ""),
            "mode": inferred_mode,
            "auth_token": token or (env_config.auth_token if env_config else ""),
            "command": command or (env_config.command if env_config else ""),
            "args": args or (list(env_config.args) if env_config else []),
            "enabled_tools": (
                list(env_config.enabled_tools) if env_config else list(DEFAULT_GITEE_READ_TOOLS)
            ),
            "timeout_seconds": env_config.timeout_seconds if env_config else 30.0,
        }
    )


def config_from_args(
    url: str | None,
    mode: str | None,
    token: str | None,
    command: str | None,
    args: list[str] | None,
) -> tuple[GiteeMCPConfig | None, dict[str, object] | None]:
    """Return ``(config, error_payload)`` for a tool invocation."""
    config = resolve_config(url, mode, token, command, args)
    if config is None:
        return None, unavailable_response(
            "gitee_mcp",
            "Gitee MCP integration is not configured. Run: opensre integrations setup gitee",
        )
    runtime_error = gitee_mcp_runtime_unavailable_reason(config)
    if runtime_error is not None:
        return None, unavailable_response("gitee_mcp", runtime_error)
    return config, None


def dispatch(
    config: GiteeMCPConfig,
    tool_name: str,
    arguments: dict[str, object],
) -> tuple[dict[str, object] | None, dict[str, object] | None]:
    """Call one MCP tool, returning ``(payload, error_payload)``."""
    try:
        result = call_gitee_mcp_tool(config, tool_name, arguments)
    except Exception as err:
        report_run_error(
            err,
            tool_name=tool_name,
            source="gitee_mcp",
            component=COMPONENT,
            method="call_gitee_mcp_tool",
            extras={"mcp_tool": tool_name, "transport": config.mode},
        )
        return None, unavailable_response(
            "gitee_mcp",
            describe_gitee_mcp_error(err, config),
            tool_name=tool_name,
            arguments=arguments,
        )

    normalized = normalize_gitee_tool_result(result)
    if not normalized.get("available"):
        return None, normalized
    return normalized, None


def decode(payload: dict[str, object]) -> object:
    """Return structured content, or JSON-decode the text payload."""
    structured = payload.get("structured_content")
    if structured is not None:
        return structured
    text = str(payload.get("text") or "").strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def truncate(text: str, limit: int) -> tuple[str, bool]:
    """Trim a string to ``limit`` characters and report whether it was cut."""
    if limit <= 0 or len(text) <= limit:
        return text, False
    return text[:limit], True


def file_view(decoded: object, *, max_chars: int) -> dict[str, object]:
    """Normalize a Gitee contents response into a directory or file view."""
    if isinstance(decoded, list):
        return {"kind": "directory", "count": len(decoded), "entries": decoded}
    if isinstance(decoded, dict):
        content = decoded.get("content")
        if isinstance(content, str) and str(decoded.get("encoding") or "").lower() == "base64":
            with contextlib.suppress(ValueError, TypeError):
                content = base64.b64decode(content).decode("utf-8", errors="replace")
        if isinstance(content, str):
            content, truncated = truncate(content, max_chars)
            return {
                "kind": str(decoded.get("type") or "file"),
                "file": decoded,
                "content": content,
                "truncated": truncated,
            }
        return {"kind": str(decoded.get("type") or "file"), "file": decoded, "content": content}
    if isinstance(decoded, str):
        content, truncated = truncate(decoded, max_chars)
        return {"kind": "file", "content": content, "truncated": truncated}
    return {"kind": "file", "content": decoded, "truncated": False}


def record(evidence: dict[str, Any], *, base: str, label: str, summary: str) -> None:
    """Record one citeable evidence entry, keeping repeated calls distinct."""
    record_evidence_entry(
        evidence,
        source=unique_evidence_source(evidence, base),
        label=label,
        summary=summary,
    )
