"""What ``opensre integrations setup mcp`` asks for one MCP server.

Each run adds or updates one named server; other servers are kept. Values are
store-only (no env mirroring), since one env var cannot hold several servers.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Mapping
from typing import Any

from config.constants.mcp import MCP_SERVERS_SERVICE
from integrations.mcp_servers.verifier import verify_mcp_server
from integrations.mcp_transport import McpTransportMode
from integrations.setup_flow import IntegrationSetupSpec, SetupField, SetupMode

NAME_FIELD = "name"
URL_FIELD = "url"
AUTH_TOKEN_FIELD = "auth_token"
COMMAND_FIELD = "command"
ARGS_FIELD = "args"
READ_ONLY_FIELD = "read_only"
ALLOWED_TOOLS_FIELD = "allowed_tools"

_SERVER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
_YES_NO = {"yes", "no", "true", "false", "y", "n"}


def _keep_unedited_settings(
    saved: Mapping[str, Any], collected: Mapping[str, str | None]
) -> dict[str, Any]:
    """Settings the form never asks about, kept when a server is updated.

    HTTP-only settings (headers, an ``sse`` transport) are kept only while the
    server still has a URL; a saved ``Authorization`` header yields to a newly
    typed bearer token.
    """
    kept: dict[str, Any] = {}
    if saved.get("timeout_seconds"):
        kept["timeout_seconds"] = saved["timeout_seconds"]
    if not collected.get(URL_FIELD):
        return kept
    if str(saved.get("mode") or "").strip().lower() == McpTransportMode.SSE.value:
        kept["mode"] = McpTransportMode.SSE.value
    headers = saved.get("headers")
    if isinstance(headers, Mapping):
        if collected.get(AUTH_TOKEN_FIELD):
            headers = {k: v for k, v in headers.items() if str(k).lower() != "authorization"}
        if headers:
            kept["headers"] = dict(headers)
    return kept


def _validate_name(value: str) -> str | None:
    if _SERVER_NAME.fullmatch(value.strip().lower()):
        return None
    return "Use 1-32 lowercase letters, digits, '-' or '_' (it prefixes the tool names)."


def _validate_url(value: str) -> str | None:
    if value.strip().startswith(("http://", "https://")):
        return None
    return "Enter the server's http:// or https:// endpoint, e.g. http://localhost:8000/mcp."


def _validate_args(value: str) -> str | None:
    try:
        shlex.split(value)
    except ValueError as exc:
        return f"Could not read the arguments ({exc}); check the quotes."
    return None


def _validate_yes_no(value: str) -> str | None:
    return None if value.strip().lower() in _YES_NO else "Answer yes or no."


MCP_SERVERS_SETUP = IntegrationSetupSpec(
    service=MCP_SERVERS_SERVICE,
    instance_field=NAME_FIELD,
    fields=(
        SetupField(
            name=NAME_FIELD,
            label="Server name",
            prompt="Server name (prefixes its tools, e.g. grafana)",
            validate=_validate_name,
        ),
        SetupField(
            name=URL_FIELD,
            label="Server URL",
            prompt="Server URL (e.g. http://localhost:8000/mcp)",
            required=False,
            validate=_validate_url,
        ),
        SetupField(
            name=AUTH_TOKEN_FIELD,
            label="Bearer token",
            prompt="Bearer token (optional, sent as Authorization)",
            required=False,
            secret=True,
        ),
        SetupField(
            name=COMMAND_FIELD,
            label="Command",
            prompt="Command that starts the server (e.g. uvx)",
            required=False,
        ),
        SetupField(
            name=ARGS_FIELD,
            label="Arguments",
            prompt="Arguments, space separated; quote any with spaces (e.g. mcp-grafana)",
            validate=_validate_args,
            prefill=shlex.join,
            required=False,
        ),
        SetupField(
            name=READ_ONLY_FIELD,
            label="Read-only",
            prompt="Expose only the tools the server marks read-only? (yes/no)",
            default="no",
            validate=_validate_yes_no,
        ),
        SetupField(
            name=ALLOWED_TOOLS_FIELD,
            label="Allowed tools",
            prompt="Only expose these tools (optional, comma separated)",
            required=False,
        ),
    ),
    mode_prompt="How does OpenSRE reach this MCP server?",
    modes=(
        SetupMode(
            value="http",
            label="Remote server (HTTP URL)",
            fields=(URL_FIELD, AUTH_TOKEN_FIELD),
            required_fields=(URL_FIELD,),
        ),
        SetupMode(
            value="stdio",
            label="Local command (stdio)",
            fields=(COMMAND_FIELD, ARGS_FIELD),
            required_fields=(COMMAND_FIELD,),
        ),
    ),
    verify=verify_mcp_server,
    carry_over=_keep_unedited_settings,
)

__all__ = ["MCP_SERVERS_SETUP"]
