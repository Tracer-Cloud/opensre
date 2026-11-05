"""Shared helpers for the Gitee MCP agent tools.

The discovery scanner skips modules whose name starts with ``_``, so this file
is importable by both tool packages without registering a tool of its own.
"""

from __future__ import annotations

import base64
import contextlib
import json
from typing import Any

from config.constants.gitee import (
    DEFAULT_GITEE_MCP_MODE,
    DEFAULT_GITEE_MCP_URL,
    DEFAULT_GITEE_READ_TOOLS,
)
from core.domain.types.evidence import record_evidence_entry, unique_evidence_source
from core.tool import report_run_error
from core.tool_framework.utils import first_string, unavailable_response
from integrations.gitee import (
    GiteeMCPConfig,
    build_gitee_mcp_config,
    call_gitee_mcp_tool,
    describe_gitee_mcp_error,
    gitee_mcp_runtime_unavailable_reason,
    normalize_gitee_tool_result,
)

COMPONENT = "integrations.gitee.tools._shared"

#: Hidden connection parameters injected by ``extract_params``. Keeping them in
#: every schema lets the tool decorator round-trip stored credentials and hides
#: them from the model-facing schema.
AUTH_PROPERTIES: dict[str, dict[str, object]] = {
    "gitee_url": {"type": "string"},
    "gitee_mode": {"type": "string"},
    "gitee_token": {"type": "string"},
    "gitee_command": {"type": "string"},
    "gitee_args": {"type": "array", "items": {"type": "string"}},
    "gitee_enabled_tools": {"type": "array", "items": {"type": "string"}},
    "gitee_timeout_seconds": {"type": "number"},
}
AUTH_PARAM_NAMES: tuple[str, ...] = tuple(AUTH_PROPERTIES)


def available(sources: dict[str, dict]) -> bool:
    """Whether the verified Gitee MCP connection is present for this turn."""
    return bool(sources.get("gitee_mcp", {}).get("connection_verified"))


def _first_string_list(source: dict[str, object], *keys: str) -> list[str]:
    """Read a list setting from a source that may hold a list, tuple, set, or CSV."""
    for key in keys:
        value = source.get(key)
        if isinstance(value, str):
            items = value.replace(",", " ").split()
        elif isinstance(value, (list, tuple, set)):
            items = [str(item) for item in value]
        else:
            continue
        cleaned = [item.strip() for item in items if item.strip()]
        if cleaned:
            return cleaned
    return []


def _first_float(source: dict[str, object], *keys: str) -> float | None:
    for key in keys:
        value = source.get(key)
        if value is None or value == "":
            continue
        try:
            return float(str(value))
        except (TypeError, ValueError):
            continue
    return None


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
        "gitee_args": _first_string_list(source, "gitee_args", "args"),
        "gitee_enabled_tools": _first_string_list(
            source, "gitee_enabled_tools", "enabled_tools"
        ),
        "gitee_timeout_seconds": _first_float(
            source, "gitee_timeout_seconds", "timeout_seconds"
        ),
    }


def resolve_config(
    url: str | None = None,
    mode: str | None = None,
    token: str | None = None,
    command: str | None = None,
    args: list[str] | None = None,
    enabled_tools: list[str] | None = None,
    timeout_seconds: float | None = None,
) -> GiteeMCPConfig:
    """Build a config from protected tool arguments, never from the process env.

    Normal runtime calls pass every value through ``extract_params``. Direct
    callers (tests, embedded use) get the documented defaults.
    """
    inferred_mode = (
        mode
        or ("stdio" if command else "")
        or ("streamable-http" if url else "")
        or DEFAULT_GITEE_MCP_MODE
    )
    return build_gitee_mcp_config(
        {
            "url": url or DEFAULT_GITEE_MCP_URL,
            "mode": inferred_mode,
            "auth_token": token or "",
            "command": command or "",
            "args": args or [],
            "enabled_tools": enabled_tools or list(DEFAULT_GITEE_READ_TOOLS),
            "timeout_seconds": timeout_seconds or 30.0,
        }
    )


def config_from_args(
    url: str | None,
    mode: str | None,
    token: str | None,
    command: str | None,
    args: list[str] | None,
    enabled_tools: list[str] | None = None,
    timeout_seconds: float | None = None,
) -> tuple[GiteeMCPConfig | None, dict[str, object] | None]:
    """Return ``(config, error_payload)`` for a tool invocation."""
    try:
        config = resolve_config(
            url,
            mode,
            token,
            command,
            args,
            enabled_tools,
            timeout_seconds,
        )
    except Exception:
        return None, unavailable_response(
            "gitee_mcp",
            "Gitee MCP connection settings are invalid. Re-run: "
            "opensre integrations setup gitee",
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
    """Normalize a Gitee contents response into a directory or file view.

    The raw ``file`` metadata intentionally excludes ``content``: the readable
    content is returned only through the bounded ``content`` field.
    """
    if isinstance(decoded, list):
        return {"kind": "directory", "count": len(decoded), "entries": decoded}
    if isinstance(decoded, dict):
        content = decoded.get("content")
        if isinstance(content, str) and str(decoded.get("encoding") or "").lower() == "base64":
            with contextlib.suppress(ValueError, TypeError):
                content = base64.b64decode(content).decode("utf-8", errors="replace")
        file_meta = {key: value for key, value in decoded.items() if key != "content"}
        if isinstance(content, str):
            content, truncated = truncate(content, max_chars)
            return {
                "kind": str(decoded.get("type") or "file"),
                "file": file_meta,
                "content": content,
                "truncated": truncated,
            }
        return {"kind": str(decoded.get("type") or "file"), "file": file_meta}
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
