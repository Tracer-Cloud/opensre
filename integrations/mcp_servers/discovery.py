"""Bounded, cached ``tools/list`` discovery for configured MCP servers.

Discovery runs while a turn's tool list is built, so it must never block a
turn for long: every server is listed on a worker thread (no event loop
there, and servers list in parallel) under one bounded wait, and each result
(including a failure) is cached per server config until it expires.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from dataclasses import dataclass
from typing import Any

from config.constants.mcp import MCP_SERVERS_INSTANCES_ENV, MCP_SERVERS_SERVICE
from integrations.mcp_client import McpSessionOptions, list_mcp_tools
from integrations.mcp_servers.config import McpServerConfig

logger = logging.getLogger(__name__)

#: How long a server's tool list is reused before it is listed again.
_LISTING_TTL_SECONDS = 300.0
#: How long a failed listing is remembered, so a down server is not retried every turn.
_FAILURE_TTL_SECONDS = 60.0
#: Upper bound on one discovery pass, whatever each server's own timeout.
_DISCOVERY_WAIT_SECONDS = 10.0


@dataclass(frozen=True)
class McpServerTool:
    """One tool a configured server advertises."""

    server: str
    name: str
    description: str
    input_schema: Mapping[str, Any]
    read_only_hint: bool


@dataclass(frozen=True)
class _Listing:
    tools: tuple[McpServerTool, ...]
    expires_at: float


_cache: dict[str, _Listing] = {}
_cache_lock = threading.Lock()


def session_options(server: str, config: McpServerConfig) -> McpSessionOptions:
    """Session options for one configured server (no vendor-specific values)."""
    return {
        "session_url": config.url,
        "stdio_env": {},
        "integration_name": f"{MCP_SERVERS_SERVICE} server '{server}'",
        "config_env_name": MCP_SERVERS_INSTANCES_ENV,
    }


def _fingerprint(server: str, config: McpServerConfig) -> str:
    payload = f"{server}\0{config.model_dump_json()}".encode()
    return hashlib.sha256(payload).hexdigest()


def _read_only_hint(tool: Any) -> bool:
    annotations = getattr(tool, "annotations", None)
    hint = getattr(annotations, "read_only_hint", None)
    if hint is None:
        hint = getattr(annotations, "readOnlyHint", None)
    return hint is True


def _list_server(server: str, config: McpServerConfig) -> tuple[McpServerTool, ...]:
    listed = list_mcp_tools(
        config, timeout_entire_operation=True, **session_options(server, config)
    )
    tools: list[McpServerTool] = []
    for tool in listed:
        read_only = _read_only_hint(tool)
        if not config.exposes(tool.name, read_only_hint=read_only):
            continue
        schema = getattr(tool, "input_schema", None) or getattr(tool, "inputSchema", None)
        tools.append(
            McpServerTool(
                server=server,
                name=tool.name,
                description=(tool.description or getattr(tool, "title", None) or tool.name).strip(),
                input_schema=schema if isinstance(schema, Mapping) else {},
                read_only_hint=read_only,
            )
        )
    return tuple(tools)


def _cached(key: str, now: float) -> tuple[McpServerTool, ...] | None:
    with _cache_lock:
        listing = _cache.get(key)
    if listing is None or listing.expires_at <= now:
        return None
    return listing.tools


def _store(key: str, tools: tuple[McpServerTool, ...], ttl: float) -> None:
    with _cache_lock:
        _cache[key] = _Listing(tools=tools, expires_at=time.monotonic() + ttl)


def discover_tools(
    servers: Sequence[tuple[str, McpServerConfig]],
) -> dict[str, tuple[McpServerTool, ...]]:
    """Return each server's exposed tools; a server that fails or is slow has none."""
    now = time.monotonic()
    found: dict[str, tuple[McpServerTool, ...]] = {}
    pending: dict[str, tuple[str, McpServerConfig]] = {}
    for server, config in servers:
        key = _fingerprint(server, config)
        cached = _cached(key, now)
        if cached is None:
            pending[key] = (server, config)
        else:
            found[server] = cached
    if not pending:
        return found

    executor = ThreadPoolExecutor(max_workers=min(8, len(pending)), thread_name_prefix="mcp-list")
    try:
        futures = {
            key: executor.submit(_list_server, server, config)
            for key, (server, config) in pending.items()
        }
        deadline = time.monotonic() + _DISCOVERY_WAIT_SECONDS
        for key, future in futures.items():
            server = pending[key][0]
            try:
                tools = future.result(timeout=max(0.0, deadline - time.monotonic()))
            except FutureTimeoutError:
                logger.warning("[mcp] listing tools for server %r timed out", server)
                _store(key, (), _FAILURE_TTL_SECONDS)
                found[server] = ()
            except Exception as exc:
                logger.warning(
                    "[mcp] listing tools for server %r failed: %s", server, type(exc).__name__
                )
                _store(key, (), _FAILURE_TTL_SECONDS)
                found[server] = ()
            else:
                _store(key, tools, _LISTING_TTL_SECONDS)
                found[server] = tools
    finally:
        # Do not wait on a hung server; its thread ends when its own timeout fires.
        executor.shutdown(wait=False, cancel_futures=True)
    return found


def clear_discovery_cache() -> None:
    """Forget every cached tool listing (tests, and after a config change)."""
    with _cache_lock:
        _cache.clear()


__all__ = ["McpServerTool", "clear_discovery_cache", "discover_tools", "session_options"]
