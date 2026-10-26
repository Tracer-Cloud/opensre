"""Tests for integration lifecycle analytics join keys."""

from __future__ import annotations

from typing import Any

from infrastructure.analytics import capture as analytics_capture
from infrastructure.analytics.repl_context import bind_cli_session_id, reset_cli_session_id


def test_integration_lifecycle_events_include_cli_session_id(monkeypatch: Any) -> None:
    captured: list[dict[str, object]] = []

    def _capture(event: object, properties: dict[str, object] | None = None) -> None:
        captured.append({"event": event, "properties": properties or {}})

    monkeypatch.setattr(analytics_capture, "_capture", _capture)

    token = bind_cli_session_id("session-abc")
    try:
        analytics_capture.capture_integration_verified("github")
    finally:
        reset_cli_session_id(token)

    assert captured
    assert captured[0]["properties"]["service"] == "github"
    assert captured[0]["properties"]["cli_session_id"] == "session-abc"


def test_integration_lifecycle_events_omit_cli_session_id_outside_repl(
    monkeypatch: Any,
) -> None:
    captured: list[dict[str, object]] = []

    def _capture(event: object, properties: dict[str, object] | None = None) -> None:
        captured.append({"event": event, "properties": properties or {}})

    monkeypatch.setattr(analytics_capture, "_capture", _capture)

    analytics_capture.capture_integration_removed("datadog")

    assert captured
    assert captured[0]["properties"]["service"] == "datadog"
    assert "cli_session_id" not in captured[0]["properties"]
