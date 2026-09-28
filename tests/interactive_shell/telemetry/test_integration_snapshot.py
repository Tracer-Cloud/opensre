"""Tests for per-turn integration snapshots on analytics capture."""

from __future__ import annotations

from typing import Any

from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.telemetry.integration_snapshot import (
    build_turn_integration_snapshot,
)


class _FakeTool:
    def __init__(self, source: str, *, available: bool = True) -> None:
        self.source = source
        self._available = available

    def is_available(self, _resolved: dict[str, Any]) -> bool:
        return self._available


def test_build_turn_integration_snapshot_empty_when_unconfigured() -> None:
    session = Session()
    session.configured_integrations_known = True
    session.configured_integrations = ()

    snapshot = build_turn_integration_snapshot(session)

    assert snapshot == {
        "connected_integrations": [],
        "connected_integrations_count": 0,
        "configured_integrations": [],
        "integration_snapshot_source": "runtime_config",
        "integration_snapshot_status": "complete",
    }


def test_build_turn_integration_snapshot_uses_session_configured_slugs(
    monkeypatch: Any,
) -> None:
    session = Session()
    session.configured_integrations_known = True
    session.configured_integrations = ("datadog", "github")
    session.resolved_integrations_cache = {
        "datadog": {"api_key": "x", "app_key": "y", "connection_verified": True},
        "github": {"access_token": "token", "connection_verified": True},
    }

    monkeypatch.setattr(
        "surfaces.interactive_shell.telemetry.integration_snapshot.get_registered_tools",
        lambda: [_FakeTool("datadog"), _FakeTool("github")],
    )

    snapshot = build_turn_integration_snapshot(session)

    assert snapshot["configured_integrations"] == ["datadog", "github"]
    assert snapshot["connected_integrations"] == ["datadog", "github"]
    assert snapshot["connected_integrations_count"] == 2


def test_build_turn_integration_snapshot_excludes_unavailable_tools(
    monkeypatch: Any,
) -> None:
    session = Session()
    session.configured_integrations_known = True
    session.configured_integrations = ("datadog", "grafana")
    session.resolved_integrations_cache = {
        "datadog": {"api_key": "x", "app_key": "y", "connection_verified": True},
        "grafana": {"endpoint": "https://grafana.example.com", "api_key": "glsa"},
    }

    monkeypatch.setattr(
        "surfaces.interactive_shell.telemetry.integration_snapshot.get_registered_tools",
        lambda: [_FakeTool("datadog"), _FakeTool("grafana", available=False)],
    )

    snapshot = build_turn_integration_snapshot(session)

    assert snapshot["configured_integrations"] == ["datadog", "grafana"]
    assert snapshot["connected_integrations"] == ["datadog"]
    assert snapshot["connected_integrations_count"] == 1


def test_build_turn_integration_snapshot_survives_tool_resolution_failure(
    monkeypatch: Any,
) -> None:
    session = Session()
    session.configured_integrations_known = True
    session.configured_integrations = ("datadog",)
    session.resolved_integrations_cache = {"datadog": {"api_key": "x", "app_key": "y"}}

    def _boom() -> list[_FakeTool]:
        raise RuntimeError("tool registry blew up")

    monkeypatch.setattr(
        "surfaces.interactive_shell.telemetry.integration_snapshot.get_registered_tools",
        _boom,
    )

    snapshot = build_turn_integration_snapshot(session)

    assert snapshot["configured_integrations"] == ["datadog"]
    assert "connected_integrations" not in snapshot
    assert "connected_integrations_count" not in snapshot
    assert snapshot["integration_snapshot_status"] == "partial"


def test_build_turn_integration_snapshot_survives_family_key_failure(
    monkeypatch: Any,
) -> None:
    session = Session()
    session.configured_integrations_known = True
    session.configured_integrations = ("datadog",)
    session.resolved_integrations_cache = {"datadog": {"api_key": "x", "app_key": "y"}}

    monkeypatch.setattr(
        "surfaces.interactive_shell.telemetry.integration_snapshot.get_registered_tools",
        lambda: [_FakeTool("datadog")],
    )

    def _boom(_service: str) -> str:
        raise RuntimeError("family key blew up")

    monkeypatch.setattr(
        "surfaces.interactive_shell.telemetry.integration_snapshot.family_key",
        _boom,
    )

    snapshot = build_turn_integration_snapshot(session)

    assert snapshot["configured_integrations"] == ["datadog"]
    assert "connected_integrations" not in snapshot
    assert "connected_integrations_count" not in snapshot
    assert snapshot["integration_snapshot_status"] == "partial"
