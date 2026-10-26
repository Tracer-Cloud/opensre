"""PostHog MCP opts into preferred sources for metric/read asks.

Core never names ``posthog_mcp``. This module is the only place that registers
it; omit the call from harness adapters (or skip loading this package) if the
product should not treat PostHog as a preferred analytics source.
"""

from __future__ import annotations

from infrastructure.harness_providers import register_preferred_evidence_source


def register_posthog_mcp_evidence_sources() -> None:
    """Advertise PostHog MCP as a preferred source for ``metric_read`` asks."""
    register_preferred_evidence_source("metric_read", "posthog_mcp")


__all__ = ["register_posthog_mcp_evidence_sources"]
