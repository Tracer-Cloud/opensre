"""Slack environment variable names."""

from __future__ import annotations

SLACK_BOT_TOKEN_ENV = "SLACK_BOT_TOKEN"
SLACK_APP_TOKEN_ENV = "SLACK_APP_TOKEN"
SLACK_ACCESS_TOKEN_ENV = "SLACK_ACCESS_TOKEN"
SLACK_DEFAULT_CHAT_ID_ENV = "SLACK_DEFAULT_CHAT_ID"
SLACK_WEBHOOK_URL_ENV = "SLACK_WEBHOOK_URL"

# Default channel for RCA report delivery.
SLACK_GITHUB_ISSUES_WEBHOOK_URL_ENV = "SLACK_GITHUB_ISSUES_WEBHOOK_URL"
# Comma-separated Slack team ids allowed to fall back to the silo organization
# when they have no install record. When set, any other team is refused instead
# of being silently attributed to the silo org (and its stored credentials).
# Unset preserves the permissive dogfood behaviour (with a warning).
SLACK_SILO_TEAM_IDS_ENV = "OPENSRE_SILO_TEAM_IDS"

# Slack user ("access") token prefixes. ``search.messages`` is user-token only,
# so callers must tell the two apart. Rotation-enabled user tokens arrive as
# ``xoxe.xoxp-``, which is why this is a tuple and not a single prefix.
SLACK_USER_TOKEN_PREFIXES: tuple[str, ...] = ("xoxp-", "xoxe.xoxp-")

# Socket Mode liveness ticker join during worker stop. Keep this small so
# in-flight Slack turns keep the rest of the SIGTERM budget.
SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS = 2.0

# Socket Mode remembers handled ``event_id`` values in process. Slack's last
# retry of an event lands minutes after the first delivery, so an hour outlives
# every retry (and matches the shared store's retention); the size cap bounds
# memory when a burst of events outruns the clock. The cap never drops an entry
# younger than the retry window, so a burst can exceed it rather than let a retry
# of a queued or running event through, but only up to the hard cap (a few MB),
# past which the oldest entries go regardless.
SLACK_SOCKET_MODE_DEDUP_TTL_SECONDS = 60 * 60
SLACK_SOCKET_MODE_DEDUP_MAX_EVENTS = 10_000
SLACK_SOCKET_MODE_DEDUP_HARD_MAX_EVENTS = 50_000
SLACK_SOCKET_MODE_DEDUP_RETRY_WINDOW_SECONDS = 10 * 60

# File hosts we may fetch with the bot token. ``url_private`` points at
# ``files.slack.com``; downloads redirect within Slack's own domains, and the
# suffix match covers those. A hop anywhere else is rejected before opening a
# connection.
SLACK_FILE_HOST_SUFFIXES: tuple[str, ...] = (
    "slack.com",
    "slack-files.com",
    "slack-edge.com",
)

__all__ = [
    "SLACK_ACCESS_TOKEN_ENV",
    "SLACK_APP_TOKEN_ENV",
    "SLACK_BOT_TOKEN_ENV",
    "SLACK_DEFAULT_CHAT_ID_ENV",
    "SLACK_FILE_HOST_SUFFIXES",
    "SLACK_GITHUB_ISSUES_WEBHOOK_URL_ENV",
    "SLACK_HEARTBEAT_STOP_TIMEOUT_SECONDS",
    "SLACK_SILO_TEAM_IDS_ENV",
    "SLACK_SOCKET_MODE_DEDUP_HARD_MAX_EVENTS",
    "SLACK_SOCKET_MODE_DEDUP_MAX_EVENTS",
    "SLACK_SOCKET_MODE_DEDUP_RETRY_WINDOW_SECONDS",
    "SLACK_SOCKET_MODE_DEDUP_TTL_SECONDS",
    "SLACK_USER_TOKEN_PREFIXES",
    "SLACK_WEBHOOK_URL_ENV",
]
