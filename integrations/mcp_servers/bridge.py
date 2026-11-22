"""Turn each configured MCP server's tools into first-class agent tools.

A bridged tool keeps the server's own name, description and input schema, so
the model calls it with typed arguments instead of through a generic
``call_*_tool`` proxy. Its side effect comes from the server's ``readOnlyHint``:
read-only tools run like any read, every other tool is ``external`` and goes
through the existing approval gates.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Mapping
from typing import Any

from config.constants.mcp import MCP_SERVER_TOOL_PREFIX, MCP_SERVERS_SERVICE
from core.domain.types.tools import ToolSurface
from core.tool import RegisteredTool, SideEffectLevel, report_run_error
from core.tool_framework.utils import unavailable_response
from integrations.mcp_client import call_mcp_tool, root_cause_message
from integrations.mcp_servers.config import McpServerConfig
from integrations.mcp_servers.discovery import McpServerTool, discover_tools, session_options
from integrations.mcp_servers.schema import bridged_input_schema
from integrations.selectors import get_instances

logger = logging.getLogger(__name__)

#: Provider tool names must match ``^[a-zA-Z0-9_-]{1,64}$`` (OpenAI-compatible subset).
_MAX_TOOL_NAME_LENGTH = 64
_INVALID_NAME_CHARS = re.compile(r"[^a-zA-Z0-9_-]+")
#: A server name that ends at the first ``__`` after the prefix, so it cannot be misread.
_UNAMBIGUOUS_SERVER = re.compile(r"[a-zA-Z0-9-]+(?:_[a-zA-Z0-9-]+)*")
_DIGEST_LENGTH = 8
_MAX_DESCRIPTION_LENGTH = 1024


def bridged_tool_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>``, provider-safe and unique per ``(server, tool)``.

    A hash suffix is added whenever the plain name could be shared by another
    pair: sanitizing changed a character, the server name could run into the
    tool name (``a__b`` + ``c`` vs ``a`` + ``b__c``), or the name was capped.
    """
    safe_server = _INVALID_NAME_CHARS.sub("_", server)
    safe_tool = _INVALID_NAME_CHARS.sub("_", tool)
    name = f"{MCP_SERVER_TOOL_PREFIX}{safe_server}__{safe_tool}"
    lossless = (
        safe_server == server
        and safe_tool == tool
        and _UNAMBIGUOUS_SERVER.fullmatch(server) is not None
    )
    if lossless and len(name) <= _MAX_TOOL_NAME_LENGTH:
        return name
    digest = hashlib.sha256(f"{server}\0{tool}".encode()).hexdigest()[:_DIGEST_LENGTH]
    return f"{name[: _MAX_TOOL_NAME_LENGTH - _DIGEST_LENGTH - 1]}_{digest}"


def _description(server: str, tool: McpServerTool) -> str:
    text = f"[{server} MCP server] {tool.description}"
    if len(text) > _MAX_DESCRIPTION_LENGTH:
        text = text[: _MAX_DESCRIPTION_LENGTH - 1] + "…"
    return text


def _runner(server: str, config: McpServerConfig, tool_name: str, name: str) -> Any:
    def run(**arguments: object) -> dict[str, object]:
        try:
            payload = call_mcp_tool(
                config,
                tool_name,
                dict(arguments),
                timeout_call=True,
                # Each call opens a fresh session; bound its startup too.
                timeout_entire_operation=True,
                **session_options(server, config),
            )
        except Exception as exc:
            report_run_error(
                exc,
                tool_name=name,
                source=MCP_SERVERS_SERVICE,
                component="integrations.mcp_servers.bridge",
                method="call_mcp_tool",
                extras={"server": server, "tool": tool_name},
            )
            message = root_cause_message(
                exc, timeout_message=f"MCP server '{server}' timed out running {tool_name}."
            )
            return unavailable_response(
                MCP_SERVERS_SERVICE,
                f"MCP server '{server}' could not run {tool_name}: {message}",
                tool_name=tool_name,
            )
        if payload.get("is_error"):
            # The executor flags failure on a truthy "error" key.
            payload["error"] = str(payload.get("text") or f"{tool_name} reported an error.")
        payload["server"] = server
        return payload

    return run


def build_bridged_tool(server: str, config: McpServerConfig, tool: McpServerTool) -> RegisteredTool:
    """One agent tool that calls ``tool`` on ``server``."""
    name = bridged_tool_name(server, tool.name)
    read_only = tool.read_only_hint
    return RegisteredTool(
        name=name,
        description=_description(server, tool),
        display_name=f"{server}: {tool.name}",
        input_schema=bridged_input_schema(tool.input_schema),
        source=MCP_SERVERS_SERVICE,
        source_id=server,
        side_effect_level=SideEffectLevel.READ_ONLY if read_only else SideEffectLevel.EXTERNAL,
        surfaces=(ToolSurface.ACTION, ToolSurface.CHAT),
        concurrency_safe=read_only,
        run=_runner(server, config, tool.name, name),
    )


def _server_config(raw: object) -> McpServerConfig | None:
    if isinstance(raw, McpServerConfig):
        return raw
    if not isinstance(raw, Mapping):
        return None
    try:
        return McpServerConfig.model_validate(
            {key: value for key, value in raw.items() if key in McpServerConfig.model_fields}
        )
    except Exception:
        logger.warning("[mcp] skipping an MCP server with an invalid config")
        return None


def configured_servers(
    resolved_integrations: Mapping[str, Any],
) -> list[tuple[str, McpServerConfig]]:
    """Every configured MCP server as ``(name, config)``, in configuration order.

    Names key discovery and tool names, so a later server reusing a name is
    skipped rather than allowed to pair its tools with the first one's config.
    """
    servers: list[tuple[str, McpServerConfig]] = []
    names: set[str] = set()
    for instance in get_instances(dict(resolved_integrations), MCP_SERVERS_SERVICE):
        config = _server_config(instance.get("config"))
        if config is None:
            continue
        name = str(instance.get("name") or "default")
        if name in names:
            logger.warning("[mcp] skipping a second MCP server named %s", name)
            continue
        names.add(name)
        servers.append((name, config))
    return servers


def mcp_server_tools(resolved_integrations: Mapping[str, Any]) -> tuple[RegisteredTool, ...]:
    """Agent tools for every tool the configured MCP servers expose this turn."""
    servers = configured_servers(resolved_integrations)
    if not servers:
        return ()
    listings = discover_tools(servers)
    tools: list[RegisteredTool] = []
    seen: set[str] = set()
    for server, config in servers:
        for tool in listings.get(server, ()):
            bridged = build_bridged_tool(server, config, tool)
            if bridged.name in seen:
                logger.warning("[mcp] skipping duplicate bridged tool name %s", bridged.name)
                continue
            seen.add(bridged.name)
            tools.append(bridged)
    return tuple(tools)


__all__ = [
    "bridged_tool_name",
    "build_bridged_tool",
    "configured_servers",
    "mcp_server_tools",
]
