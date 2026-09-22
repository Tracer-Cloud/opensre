"""Paths and limits for talking to the organization's hosted gateway through the OpenSRE app."""

from __future__ import annotations

HOSTED_GATEWAY_HEALTH_PATH = "/api/agent-backend/gateway/health"
HOSTED_GATEWAY_START_PATH = "/api/agent-backend/gateway/start"
HOSTED_GATEWAY_STOP_PATH = "/api/agent-backend/gateway/stop"
HOSTED_GATEWAY_PROMPTS_PATH = "/api/agent-backend/gateway/prompts"
#: How long the prompt tool waits for the remote turn before handing back the prompt id.
HOSTED_GATEWAY_PROMPT_WAIT_SECONDS = 600.0
HOSTED_GATEWAY_PROMPT_POLL_SECONDS = 3.0
# Where an organization admin provisions and inspects the gateway in the app.
HOSTED_GATEWAY_SETTINGS_PATH = "/settings/agent-backend"
HOSTED_GATEWAY_HTTP_TIMEOUT_SECONDS = 30.0
# Hosts the account token may be sent to over plain http (local development only).
HOSTED_GATEWAY_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

__all__ = [
    "HOSTED_GATEWAY_HEALTH_PATH",
    "HOSTED_GATEWAY_HTTP_TIMEOUT_SECONDS",
    "HOSTED_GATEWAY_LOOPBACK_HOSTS",
    "HOSTED_GATEWAY_SETTINGS_PATH",
    "HOSTED_GATEWAY_START_PATH",
    "HOSTED_GATEWAY_PROMPTS_PATH",
    "HOSTED_GATEWAY_PROMPT_POLL_SECONDS",
    "HOSTED_GATEWAY_PROMPT_WAIT_SECONDS",
    "HOSTED_GATEWAY_STOP_PATH",
]
