"""Per-turn integration snapshots for analytics capture."""

from __future__ import annotations

from typing import Any, Protocol

from core.domain.alerts.alert_source import secondary_tool_sources
from integrations.registry import family_key
from tools.registry import get_registered_tools


class _IntegrationSession(Protocol):
    configured_integrations: tuple[str, ...]
    configured_integrations_known: bool
    resolved_integrations_cache: dict[str, Any] | None


def build_turn_integration_snapshot(session: _IntegrationSession | None) -> dict[str, Any]:
    """Return analytics-friendly integration state for one LLM generation turn."""
    configured = _configured_slugs(session)
    snapshot: dict[str, Any] = {
        "integration_snapshot_source": "runtime_config",
        "integration_snapshot_status": "unavailable",
    }
    if configured is None:
        return snapshot
    snapshot["configured_integrations"] = configured
    connected = _connected_slugs(configured, _resolved_integrations(session))
    snapshot["integration_snapshot_status"] = "partial" if connected is None else "complete"
    if connected is not None:
        snapshot["connected_integrations"] = connected
        snapshot["connected_integrations_count"] = len(connected)
    return snapshot


def _configured_slugs(session: _IntegrationSession | None) -> list[str] | None:
    if session is not None and session.configured_integrations_known:
        return sorted(session.configured_integrations)
    try:
        from integrations.verify import resolve_effective_integrations

        return sorted(resolve_effective_integrations())
    except Exception:
        return None


def _resolved_integrations(session: _IntegrationSession | None) -> dict[str, Any] | None:
    if session is not None and session.resolved_integrations_cache is not None:
        return session.resolved_integrations_cache
    try:
        from core.agent_harness.spi.integrations import resolve_integrations

        return resolve_integrations()
    except Exception:
        return None


def _connected_slugs(configured: list[str], resolved: dict[str, Any] | None) -> list[str] | None:
    if not configured:
        return []
    if resolved is None:
        return None
    if not resolved:
        return []
    try:
        tools = [tool for tool in get_registered_tools() if tool.is_available(resolved)]
        secondary = secondary_tool_sources()
        active_families = {
            family_key(str(tool.source)) for tool in tools if str(tool.source) not in secondary
        }
        if not active_families:
            return []
        return sorted(svc for svc in configured if family_key(svc) in active_families)
    except Exception:
        return None
