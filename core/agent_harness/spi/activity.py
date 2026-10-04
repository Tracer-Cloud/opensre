"""Plain-text tool activity a host shows while a turn is running.

Rich painting stays in the interactive shell. Hosts share this wording so a
gateway tool line cannot drift from the local one.
"""

from __future__ import annotations

from core.agent_harness.activity_display import (
    HostedActivity,
    bounded_activity_preview,
    format_hosted_activity,
    generic_tool_activity,
    github_cli_activity,
    is_sensitive_activity_key,
)

__all__ = [
    "HostedActivity",
    "bounded_activity_preview",
    "format_hosted_activity",
    "generic_tool_activity",
    "github_cli_activity",
    "is_sensitive_activity_key",
]
