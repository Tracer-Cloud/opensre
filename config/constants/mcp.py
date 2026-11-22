"""Constants shared by Model Context Protocol integrations."""

from __future__ import annotations

from typing import Final

MCP_NO_COLOR_ENV = "NO_COLOR"
MCP_TERMINAL_ENV = "TERM"
MCP_TERMINAL_DUMB_VALUE = "dumb"

#: Integration service name for user-configured MCP servers (one instance per server).
MCP_SERVERS_SERVICE: Final[str] = "mcp"
#: JSON array of MCP server instances, the env form of the ``mcp`` integration.
MCP_SERVERS_INSTANCES_ENV: Final[str] = "MCP_INSTANCES"
#: Prefix of every tool bridged from a configured MCP server: ``mcp__<server>__<tool>``.
MCP_SERVER_TOOL_PREFIX: Final[str] = "mcp__"
#: Environment variables the official ``mcp-grafana`` server reads at startup.
MCP_GRAFANA_URL_ENV: Final[str] = "GRAFANA_URL"
MCP_GRAFANA_TOKEN_ENV: Final[str] = "GRAFANA_SERVICE_ACCOUNT_TOKEN"
#: Environment variables PagerDuty's ``pagerduty-mcp`` server reads.
MCP_PAGERDUTY_TOKEN_ENV: Final[str] = "PAGERDUTY_USER_API_KEY"
MCP_PAGERDUTY_HOST_ENV: Final[str] = "PAGERDUTY_API_HOST"
#: Named AWS profile for the awslabs MCP servers (region uses ``AWS_REGION``).
MCP_AWS_PROFILE_ENV: Final[str] = "AWS_PROFILE"
#: awslabs ECS MCP server switches; both are kept ``false`` by its preset.
MCP_ECS_ALLOW_WRITE_ENV: Final[str] = "ALLOW_WRITE"
MCP_ECS_ALLOW_SENSITIVE_DATA_ENV: Final[str] = "ALLOW_SENSITIVE_DATA"
#: HTTP headers Datadog's managed MCP server accepts for key-based auth.
MCP_DATADOG_API_KEY_HEADER: Final[str] = "DD_API_KEY"
MCP_DATADOG_APP_KEY_HEADER: Final[str] = "DD_APPLICATION_KEY"
