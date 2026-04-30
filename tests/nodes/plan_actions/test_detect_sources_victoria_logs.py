"""Tests for VictoriaLogs source detection in detect_sources.

Without the ``victoria_logs`` block in ``detect_sources.py``,
``available_sources`` never gets a ``"victoria_logs"`` key, so
``VictoriaLogsTool.is_available()`` always returns ``False`` and the tool
is never selected during an investigation. These tests guard against that
regression — every other tool-backed integration has the same wiring, and
the absence of a block has been a recurring P1 in prior PRs (#663, #1060).
"""

from __future__ import annotations

from app.nodes.plan_actions.detect_sources import detect_sources

_VICTORIA_LOGS_INT = {
    "base_url": "http://vmlogs:9428",
    "tenant_id": None,
    "integration_id": "vl-1",
}


def test_victoria_logs_source_detected_when_configured() -> None:
    alert = {"annotations": {"alertname": "HighErrorRate"}}

    sources = detect_sources(alert, {}, {"victoria_logs": _VICTORIA_LOGS_INT})

    victoria = sources.get("victoria_logs")
    assert victoria is not None, (
        "victoria_logs must be populated in available_sources, otherwise "
        "VictoriaLogsTool.is_available() returns False forever and the "
        "tool is never scheduled"
    )
    assert victoria["base_url"] == "http://vmlogs:9428"
    assert victoria["tenant_id"] is None
    assert victoria["connection_verified"] is True


def test_victoria_logs_source_with_tenant_id() -> None:
    integration = {**_VICTORIA_LOGS_INT, "tenant_id": "team-a"}
    sources = detect_sources({}, {}, {"victoria_logs": integration})

    victoria = sources.get("victoria_logs")
    assert victoria is not None
    assert victoria["tenant_id"] == "team-a"


def test_victoria_logs_skipped_when_base_url_missing() -> None:
    integration = {**_VICTORIA_LOGS_INT, "base_url": ""}
    sources = detect_sources({}, {}, {"victoria_logs": integration})

    assert "victoria_logs" not in sources


def test_victoria_logs_skipped_when_integration_absent() -> None:
    sources = detect_sources({}, {}, {})

    assert "victoria_logs" not in sources
