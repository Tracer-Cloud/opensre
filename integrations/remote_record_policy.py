"""What an integration record written somewhere else may ask this machine to do.

A record made on the machine that uses it can name a program to run or a file
to read: a stdio MCP server, a ``helm`` binary, a kubeconfig with an ``exec``
plugin. Those belong to the person at the keyboard.

A record that arrives from the OpenSRE app, or any record on a host that
serves an organization, was written by someone who does not own this machine.
For those records the same fields are command execution and file reads on a
host that is not theirs, and an address inside the host's own network is a
request the organization could never make itself. So a remote record may name
remote services, and nothing on or beside this machine.

The address check resolves a name once, when the record is classified. It does
not pin the connection to that answer, so it is a filter in front of the
network's own egress rules, not a replacement for them.
"""

from __future__ import annotations

import logging
import socket
import time
from collections.abc import Mapping
from ipaddress import IPv4Address, IPv6Address, ip_address
from typing import Any
from urllib.parse import parse_qs, unquote, urlsplit

import yaml

from config.constants.account import INTEGRATION_APP_ORIGIN, INTEGRATION_RETRIEVAL_ORIGIN_FIELD
from config.constants.organization import organization_id

logger = logging.getLogger(__name__)

_STDIO_MODE = "stdio"
# A local program's name and arguments.
_COMMAND_FIELDS = frozenset({"command", "args"})
# A path to an executable or a file on this machine.
_LOCAL_PATH_FIELDS = frozenset(
    {"buzz_path", "credentials_file", "helm_path", "kubeconfig_path", "railway_path"}
)
# Services whose record is nothing but a transport: without a URL nothing is left.
_TRANSPORT_ONLY_SERVICES = frozenset({"mcp", "posthog_mcp", "sentry_mcp"})
# Helm passes ``kubeconfig`` to ``--kubeconfig``, so there it is a path too.
_KUBECONFIG_IS_A_PATH = frozenset({"helm"})

# Kubeconfig per-user fields that run a program or read a local file.
_KUBECONFIG_USER_FIELDS = (
    "exec",
    "auth-provider",
    "tokenFile",
    "client-certificate",
    "client-key",
)
# Kubeconfig per-cluster fields that read a local file.
_KUBECONFIG_CLUSTER_FIELDS = ("certificate-authority",)

# Fields that carry a bare host name (or a comma-separated list of them).
_HOST_FIELDS = frozenset({"bootstrap_servers", "host", "hostname", "hosts", "server"})
# Query parameters a database driver reads the server from.
_SERVER_QUERY_FIELDS = ("host", "hostaddr")
_INTERNAL_NAME_SUFFIXES = (".internal", ".local", ".localhost")
_RESOLUTION_TTL_SECONDS = 300.0
_resolved_internal: dict[str, tuple[float, bool]] = {}


def is_remote_record(record: Mapping[str, Any]) -> bool:
    """Whether ``record`` was written by someone who does not own this machine."""
    if record.get(INTEGRATION_RETRIEVAL_ORIGIN_FIELD) == INTEGRATION_APP_ORIGIN:
        return True
    return bool(organization_id())


def remote_credentials(service: str, credentials: Mapping[str, Any]) -> dict[str, Any] | None:
    """``credentials`` with every local program and file removed.

    Returns None when what is left cannot be used: the record only described a
    local transport, its kubeconfig reaches into the host, or (on a host
    serving an organization) one of its addresses is inside the host's network.
    """
    wants_stdio = str(credentials.get("mode") or "").strip().lower() == _STDIO_MODE
    names_command = bool(str(credentials.get("command") or "").strip())
    kept = {
        name: value
        for name, value in credentials.items()
        if name not in _COMMAND_FIELDS and name not in _LOCAL_PATH_FIELDS
    }
    if wants_stdio:
        kept.pop("mode", None)
    if service in _KUBECONFIG_IS_A_PATH:
        kept.pop("kubeconfig", None)

    reason = _refusal(service, kept, local_only=wants_stdio or names_command)
    if reason is not None:
        # The reason names a field, never a value: values here are credentials.
        logger.warning("Ignoring the %s integration record: %s.", service, reason)
        return None
    return kept


def _refusal(service: str, kept: Mapping[str, Any], *, local_only: bool) -> str | None:
    """Why what is left of a remote record cannot be used, or None when it can."""
    if (
        service in _TRANSPORT_ONLY_SERVICES
        and local_only
        and not str(kept.get("url") or "").strip()
    ):
        return "it only describes a local program"

    kubeconfig = kept.get("kubeconfig")
    if isinstance(kubeconfig, str) and kubeconfig.strip():
        reason = _kubeconfig_refusal(kubeconfig)
        if reason is not None:
            return reason

    if organization_id():
        field = _internal_address_field(kept)
        if field is not None:
            return f"`{field}` is an address inside this host's network"
    return None


def _kubeconfig_refusal(kubeconfig: str) -> str | None:
    """Why an inline kubeconfig may not be loaded here, or None when it may."""
    try:
        document = yaml.safe_load(kubeconfig)
    except yaml.YAMLError:
        return "its kubeconfig is not valid YAML"
    if not isinstance(document, Mapping):
        return "its kubeconfig is not a mapping"
    for entry, section, fields in (
        ("users", "user", _KUBECONFIG_USER_FIELDS),
        ("clusters", "cluster", _KUBECONFIG_CLUSTER_FIELDS),
    ):
        items = document.get(entry)
        for item in items if isinstance(items, list) else ():
            body = item.get(section) if isinstance(item, Mapping) else None
            if not isinstance(body, Mapping):
                continue
            for name in fields:
                if body.get(name):
                    return (
                        f"its kubeconfig uses `{entry}[].{section}.{name}`, which runs a "
                        "program or reads a file on this host; use an embedded token or "
                        "embedded `*-data` certificates"
                    )
            server = body.get("server")
            if organization_id() and isinstance(server, str) and _address_is_internal(server):
                return "its kubeconfig cluster server is an address inside this host's network"
    return None


def _internal_address_field(credentials: Mapping[str, Any]) -> str | None:
    """The first field whose address is inside this host's network, else None."""
    for name, value in credentials.items():
        if not isinstance(value, str) or not value.strip():
            continue
        if "://" in value:
            if _address_is_internal(value):
                return name
        elif name in _HOST_FIELDS and any(
            _server_is_internal(server) for server in value.split(",")
        ):
            return name
    return None


def _address_is_internal(url: str) -> bool:
    """Whether any server ``url`` names is inside this host's network."""
    try:
        parts = urlsplit(url.strip())
        query = parse_qs(parts.query)
    except ValueError:
        # An address that cannot be read cannot be shown to be outside.
        return True
    # A connection string may list several servers (``mongodb://a,b/``), and a
    # driver may take the server from the query instead (``?host=``).
    named = parts.netloc.rpartition("@")[2].split(",")
    for name in _SERVER_QUERY_FIELDS:
        for value in query.get(name, ()):
            named.extend(value.split(","))
    return any(_server_is_internal(server) for server in named)


def _server_is_internal(server: str) -> bool:
    """Whether one ``host`` or ``host:port`` entry of a connection string is internal."""
    text = unquote(server).strip()
    # A path is a local socket: a service on this machine, not a remote one.
    return text.startswith("/") or _host_is_internal(_bare_host(text))


def _bare_host(value: str) -> str:
    """``host`` out of ``host``, ``host:port`` or ``[v6]:port``."""
    text = value.strip()
    if text.startswith("["):
        return text[1:].split("]", 1)[0]
    if text.count(":") == 1:
        return text.split(":", 1)[0]
    return text


def _host_is_internal(host: str) -> bool:
    name = host.strip().strip("[]").rstrip(".").lower()
    if not name:
        return False
    if name == "localhost" or name.endswith(_INTERNAL_NAME_SUFFIXES):
        return True
    try:
        return _ip_is_internal(ip_address(name))
    except ValueError:
        return _name_resolves_internal(name)


def _ip_is_internal(address: IPv4Address | IPv6Address) -> bool:
    mapped = address.ipv4_mapped if isinstance(address, IPv6Address) else None
    return not (mapped or address).is_global


def _name_resolves_internal(name: str) -> bool:
    """Whether any address ``name`` resolves to is internal; unknown names are not."""
    now = time.monotonic()
    cached = _resolved_internal.get(name)
    if cached is not None and now - cached[0] < _RESOLUTION_TTL_SECONDS:
        return cached[1]
    try:
        answers = socket.getaddrinfo(name, None, proto=socket.IPPROTO_TCP)
    except (OSError, UnicodeError):
        # The client that would use this name cannot reach it either. Remembered
        # like any other answer, so a dead name is not looked up on every turn.
        internal = False
    else:
        internal = any(_ip_is_internal(ip_address(str(answer[4][0]))) for answer in answers)
    _resolved_internal[name] = (now, internal)
    return internal


__all__ = ["is_remote_record", "remote_credentials"]
