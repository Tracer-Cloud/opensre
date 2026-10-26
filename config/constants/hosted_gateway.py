"""Paths and limits for talking to the organization's hosted gateway through the OpenSRE app."""

from __future__ import annotations

HOSTED_GATEWAY_HEALTH_PATH = "/api/agent-backend/gateway/health"
HOSTED_GATEWAY_START_PATH = "/api/agent-backend/gateway/start"
HOSTED_GATEWAY_STOP_PATH = "/api/agent-backend/gateway/stop"
HOSTED_GATEWAY_PROMPTS_PATH = "/api/agent-backend/gateway/prompts"
#: Where an organization admin configures the integrations the hosted gateway uses.
HOSTED_GATEWAY_INTEGRATIONS_PATH = "/integrations"
#: How long the prompt tool waits for the remote turn before handing back the prompt id.
HOSTED_GATEWAY_PROMPT_WAIT_SECONDS = 600.0
HOSTED_GATEWAY_PROMPT_POLL_SECONDS = 3.0
#: After this long in the gateway's queue the user is told they are waiting for a free slot.
HOSTED_GATEWAY_QUEUE_NOTICE_SECONDS = 10.0
#: How long a prompt's wait rides out a gateway that stopped answering before handing back
#: the prompt id. Covers a relay blip; a restart takes longer and drops the prompt anyway.
HOSTED_GATEWAY_UNANSWERED_GRACE_SECONDS = 60.0
#: Pauses before each retry of a send or first read the gateway did not answer (502/503/504,
#: connection refused or timed out). Every retry reuses the call's request id, so nothing is
#: queued twice.
HOSTED_GATEWAY_SUBMIT_RETRY_DELAYS_SECONDS: tuple[float, ...] = (3.0, 6.0, 12.0, 24.0)
#: No send or first-read retry starts after this many seconds. A request already in
#: flight still runs to its HTTP timeout, so a gateway that times out every request is
#: reported after at most this budget plus one attempt.
HOSTED_GATEWAY_SUBMIT_RETRY_BUDGET_SECONDS = 60.0
# Where an organization admin provisions and inspects the gateway in the app.
HOSTED_GATEWAY_SETTINGS_PATH = "/settings/agent-backend"
HOSTED_GATEWAY_HTTP_TIMEOUT_SECONDS = 30.0
#: A TCP + TLS handshake to the app takes well under a second; past this it is stuck, and
#: the client tries one fresh connection rather than waiting out the full timeout.
HOSTED_GATEWAY_CONNECT_TIMEOUT_SECONDS = 10.0
# Hosts the account token may be sent to over plain http (local development only).
HOSTED_GATEWAY_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})

__all__ = [
    "HOSTED_GATEWAY_CONNECT_TIMEOUT_SECONDS",
    "HOSTED_GATEWAY_HEALTH_PATH",
    "HOSTED_GATEWAY_HTTP_TIMEOUT_SECONDS",
    "HOSTED_GATEWAY_LOOPBACK_HOSTS",
    "HOSTED_GATEWAY_SETTINGS_PATH",
    "HOSTED_GATEWAY_START_PATH",
    "HOSTED_GATEWAY_INTEGRATIONS_PATH",
    "HOSTED_GATEWAY_PROMPTS_PATH",
    "HOSTED_GATEWAY_PROMPT_POLL_SECONDS",
    "HOSTED_GATEWAY_PROMPT_WAIT_SECONDS",
    "HOSTED_GATEWAY_QUEUE_NOTICE_SECONDS",
    "HOSTED_GATEWAY_STOP_PATH",
    "HOSTED_GATEWAY_SUBMIT_RETRY_BUDGET_SECONDS",
    "HOSTED_GATEWAY_SUBMIT_RETRY_DELAYS_SECONDS",
    "HOSTED_GATEWAY_UNANSWERED_GRACE_SECONDS",
]
