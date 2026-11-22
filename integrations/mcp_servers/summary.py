"""Display-safe summaries of configured MCP servers.

Lists show which servers exist and how they are reached, never how they
authenticate: tokens, headers, URL query strings and user info, and command
arguments (which may carry ``--token …``) are left out.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from config.constants.mcp import MCP_SERVERS_SERVICE
from integrations.mcp_transport import McpTransportMode


@dataclass(frozen=True)
class McpServerSummary:
    """How one configured MCP server is reached, without its credentials."""

    name: str
    transport: str
    endpoint: str
    read_only: bool


def _safe_url(url: str) -> str:
    """Scheme, host, port and path only; an unparseable URL shows as ``<invalid url>``."""
    try:
        parts = urlsplit(url.strip())
        host = parts.hostname or ""
        if ":" in host:
            host = f"[{host}]"
        if parts.port:
            host = f"{host}:{parts.port}"
    except ValueError:
        return "<invalid url>"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


def _summary(name: str, config: Mapping[str, Any]) -> McpServerSummary:
    url = str(config.get("url") or "").strip()
    command = str(config.get("command") or "").strip()
    mode = str(config.get("mode") or "").strip().lower()
    if not mode:
        mode = McpTransportMode.STDIO.value if command and not url else "streamable-http"
    if mode == McpTransportMode.STDIO.value:
        endpoint = os.path.basename(command) or "-"
    else:
        endpoint = _safe_url(url) if url else "-"
    read_only = str(config.get("read_only") or "").strip().lower() in {"1", "true", "yes", "y"}
    return McpServerSummary(name=name, transport=mode, endpoint=endpoint, read_only=read_only)


def server_summaries(effective_integrations: Mapping[str, Any]) -> list[McpServerSummary]:
    """One summary per configured MCP server, in configuration order."""
    entry = effective_integrations.get(MCP_SERVERS_SERVICE)
    if not isinstance(entry, Mapping):
        return []
    instances = entry.get("instances")
    if isinstance(instances, list) and instances:
        return [
            _summary(str(inst.get("name") or "default"), inst.get("config") or {})
            for inst in instances
            if isinstance(inst, Mapping)
        ]
    config = entry.get("config")
    return [_summary("default", config)] if isinstance(config, Mapping) else []


__all__ = ["McpServerSummary", "server_summaries"]
