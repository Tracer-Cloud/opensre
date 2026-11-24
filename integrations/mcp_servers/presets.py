"""Catalog of presets for official MCP servers, and how a preset becomes a config.

A preset is data: the answers ``opensre integrations setup mcp`` asks for it and
the server those answers start. A configured preset is stored as its own
answers, so re-running setup prefills them; the answers are expanded into
``command``/``args``/``env`` (local servers) or ``url``/``headers`` (remote
servers) each time a server config is built. Credentials therefore reach a
server only through its environment or request headers, never tool arguments.

Commands, environment variables and headers follow each vendor's own docs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from config.constants.aws import AWS_REGION_ENV
from config.constants.mcp import (
    MCP_AWS_PROFILE_ENV,
    MCP_DATADOG_API_KEY_HEADER,
    MCP_DATADOG_APP_KEY_HEADER,
    MCP_ECS_ALLOW_SENSITIVE_DATA_ENV,
    MCP_ECS_ALLOW_WRITE_ENV,
    MCP_GRAFANA_TOKEN_ENV,
    MCP_GRAFANA_URL_ENV,
    MCP_PAGERDUTY_HOST_ENV,
    MCP_PAGERDUTY_TOKEN_ENV,
)
from integrations.mcp_transport import McpTransportMode


@dataclass(frozen=True)
class PresetField:
    """One setup answer a preset asks for, and where its value goes."""

    name: str
    """Credential key the answer is stored under; unique across the catalog."""
    label: str
    prompt: str
    env: str = ""
    """Environment variable the value is passed in, for a local server."""
    header: str = ""
    """HTTP header the value is sent in, for a remote server."""
    secret: bool = False
    required: bool = True
    default: str = ""
    """Value used when the answer is blank."""
    choices: tuple[str, ...] = ()
    """Allowed values; anything else is refused at setup and when the config is built."""
    is_url: bool = False


@dataclass(frozen=True)
class PresetServer:
    """How a preset reaches its server: a local command, or a remote URL."""

    command: str = ""
    args: tuple[str, ...] = ()
    read_only_args: tuple[str, ...] = ()
    """Arguments added when the server is set up read-only."""
    env: Mapping[str, str] = field(default_factory=dict)
    """Fixed environment for the command."""
    url: str = ""
    """Remote endpoint; ``{field}`` placeholders take the preset's answers."""


@dataclass(frozen=True)
class McpPreset:
    """One official MCP server OpenSRE can set up from a few answers."""

    key: str
    label: str
    fields: tuple[PresetField, ...]
    servers: Mapping[str, PresetServer]
    """Keyed by the ``variant_field`` answer, or ``""`` for a single server."""
    variant_field: str = ""
    """Answer that picks one of several servers (the AWS server to run)."""

    @property
    def marker(self) -> str:
        """The required answer whose presence means this preset is configured."""
        return self.variant_field or self.fields[0].name


AWS_SERVER_FIELD = "aws_mcp_server"

PRESETS: tuple[McpPreset, ...] = (
    McpPreset(
        key="grafana",
        label="Grafana (official mcp-grafana server, run with uvx)",
        fields=(
            PresetField(
                name="grafana_url",
                label="Grafana URL",
                prompt="Grafana URL (e.g. http://localhost:3000 or https://myorg.grafana.net)",
                env=MCP_GRAFANA_URL_ENV,
                is_url=True,
            ),
            PresetField(
                name="grafana_service_account_token",
                label="Grafana service account token",
                prompt="Grafana service account token",
                env=MCP_GRAFANA_TOKEN_ENV,
                secret=True,
            ),
        ),
        servers={
            "": PresetServer(
                command="uvx", args=("mcp-grafana",), read_only_args=("--disable-write",)
            )
        },
    ),
    McpPreset(
        key="datadog",
        label="Datadog (Datadog's managed MCP server)",
        fields=(
            PresetField(
                name="datadog_api_key",
                label="Datadog API key",
                prompt="Datadog API key",
                header=MCP_DATADOG_API_KEY_HEADER,
                secret=True,
            ),
            PresetField(
                name="datadog_application_key",
                label="Datadog application key",
                prompt="Datadog application key",
                header=MCP_DATADOG_APP_KEY_HEADER,
                secret=True,
            ),
            PresetField(
                name="datadog_site",
                label="Datadog site",
                prompt="Datadog site (optional, default datadoghq.com; e.g. datadoghq.eu)",
                required=False,
                default="datadoghq.com",
                choices=(
                    "datadoghq.com",
                    "us3.datadoghq.com",
                    "us5.datadoghq.com",
                    "datadoghq.eu",
                    "ap1.datadoghq.com",
                    "ap2.datadoghq.com",
                    "uk1.datadoghq.com",
                ),
            ),
        ),
        servers={"": PresetServer(url="https://mcp.{datadog_site}/v1/mcp")},
    ),
    McpPreset(
        key="pagerduty",
        label="PagerDuty (official pagerduty-mcp server, run with uvx)",
        fields=(
            PresetField(
                name="pagerduty_user_api_token",
                label="PagerDuty user API token",
                prompt="PagerDuty user API token",
                env=MCP_PAGERDUTY_TOKEN_ENV,
                secret=True,
            ),
            PresetField(
                name="pagerduty_api_host",
                label="PagerDuty API host",
                prompt="PagerDuty API host (optional; https://api.eu.pagerduty.com for EU accounts)",
                env=MCP_PAGERDUTY_HOST_ENV,
                required=False,
                is_url=True,
            ),
        ),
        # Read-only by default; write tools need --enable-write-tools, never added here.
        servers={"": PresetServer(command="uvx", args=("pagerduty-mcp",))},
    ),
    McpPreset(
        key="aws",
        label="AWS (official awslabs MCP servers, run with uvx)",
        fields=(
            PresetField(
                name=AWS_SERVER_FIELD,
                label="AWS MCP server",
                prompt="AWS MCP server (cloudwatch, ecs, eks or prometheus)",
                choices=("cloudwatch", "ecs", "eks", "prometheus"),
            ),
            PresetField(
                name="aws_mcp_region",
                label="AWS region",
                prompt="AWS region (optional, e.g. us-east-1)",
                env=AWS_REGION_ENV,
                required=False,
            ),
            PresetField(
                name="aws_mcp_profile",
                label="AWS profile",
                prompt="AWS profile (optional; else the default credential chain)",
                env=MCP_AWS_PROFILE_ENV,
                required=False,
            ),
        ),
        variant_field=AWS_SERVER_FIELD,
        servers={
            "cloudwatch": PresetServer(
                command="uvx", args=("awslabs.cloudwatch-mcp-server@latest",)
            ),
            # Write and sensitive-data tools stay off; they need explicit opt-in.
            "ecs": PresetServer(
                command="uvx",
                args=("--from", "awslabs-ecs-mcp-server", "ecs-mcp-server"),
                env={MCP_ECS_ALLOW_WRITE_ENV: "false", MCP_ECS_ALLOW_SENSITIVE_DATA_ENV: "false"},
            ),
            # Read-only by default; writes need --allow-write, never added here.
            "eks": PresetServer(command="uvx", args=("awslabs.eks-mcp-server@latest",)),
            "prometheus": PresetServer(
                command="uvx", args=("awslabs.prometheus-mcp-server@latest",)
            ),
        },
    ),
)

_TRUE = frozenset({"yes", "y", "true", "1"})


def _is_true(value: object) -> bool:
    return value is True or str(value or "").strip().lower() in _TRUE


def _apply(preset: McpPreset, answers: Mapping[str, str], data: dict[str, Any]) -> dict[str, Any]:
    for spec in preset.fields:
        if spec.choices and answers[spec.name] and answers[spec.name] not in spec.choices:
            raise ValueError(f"{spec.label} must be one of: {', '.join(spec.choices)}.")
    server = preset.servers.get(answers.get(preset.variant_field, ""))
    if server is None:
        raise ValueError(f"Unknown {preset.key} MCP server.")
    if server.url:
        headers = dict(data.get("headers") or {})
        headers.update(
            {f.header: answers[f.name] for f in preset.fields if f.header and answers[f.name]}
        )
        data.update(
            mode=McpTransportMode.STREAMABLE_HTTP.value,
            url=server.url.format(**answers),
            command="",
            args=[],
            headers=headers,
        )
        return data
    env = dict(data.get("env") or {})
    env.update(server.env)
    env.update({f.env: answers[f.name] for f in preset.fields if f.env and answers[f.name]})
    args = list(server.args)
    if _is_true(data.get("read_only")):
        args.extend(server.read_only_args)
    data.update(
        mode=McpTransportMode.STDIO.value, url="", command=server.command, args=args, env=env
    )
    return data


def expand_presets(raw: Mapping[str, Any]) -> dict[str, Any]:
    """``raw`` with any preset answers replaced by the server settings they imply.

    Raises ``ValueError`` for an answer outside a preset's allowed choices, or
    when answers for more than one preset are present.
    """
    data = dict(raw)
    stored = {
        spec.name: str(data.pop(spec.name, "") or "").strip()
        for preset in PRESETS
        for spec in preset.fields
    }
    chosen = [preset for preset in PRESETS if stored.get(preset.marker)]
    if len(chosen) > 1:
        names = ", ".join(preset.key for preset in chosen)
        raise ValueError(f"Configure one MCP preset per server, not several ({names}).")
    if not chosen:
        return data
    [preset] = chosen
    answers = {spec.name: stored[spec.name] or spec.default for spec in preset.fields}
    return _apply(preset, answers, data)


__all__ = [
    "AWS_SERVER_FIELD",
    "PRESETS",
    "McpPreset",
    "PresetField",
    "PresetServer",
    "expand_presets",
]
