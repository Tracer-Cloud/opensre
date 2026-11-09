"""Tests for list_sentry_uptime_alerts tool."""

from __future__ import annotations

from typing import Any

from integrations.sentry.tools.sentry_list_uptime_alerts_tool import (
    _map_list_sentry_uptime_alerts,
    list_sentry_uptime_alerts,
)
from integrations.sentry.uptime import UptimeMonitor


def test_list_sentry_uptime_alerts_returns_normalized_rows(monkeypatch) -> None:
    monkeypatch.setattr(
        "integrations.sentry.tools.sentry_list_uptime_alerts_tool._resolve_config",
        lambda *_args, **_kwargs: object(),
    )
    monkeypatch.setattr(
        "integrations.sentry.tools.sentry_list_uptime_alerts_tool.list_sentry_uptime_monitors",
        lambda **_kwargs: [
            UptimeMonitor(
                id="1",
                name="api",
                url="https://api.example.com",
                project_slug="web",
                health="down",
                uptime_status=2,
                status="active",
            )
        ],
    )

    result = list_sentry_uptime_alerts(organization_slug="acme", sentry_token="tok")
    assert result["available"] is True
    assert result["down_count"] == 1
    assert result["monitors"][0]["severity"] == "critical"
    assert result["monitors"][0]["health"] == "down"


def test_list_sentry_uptime_alerts_missing_creds(monkeypatch) -> None:
    monkeypatch.setattr(
        "integrations.sentry.tools.sentry_list_uptime_alerts_tool.sentry_config_from_env",
        lambda: None,
    )
    result = list_sentry_uptime_alerts(organization_slug="", sentry_token="")
    assert result["available"] is False
    assert "error" in result


class TestMapListSentryUptimeAlerts:
    def test_records_entry_with_down_count(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_sentry_uptime_alerts(
            evidence,
            {
                "available": True,
                "monitor_count": 3,
                "down_count": 1,
                "monitors": [{"name": "api", "health": "down"}],
            },
            {},
        )

        entries = evidence["catalog_entries"]
        assert len(entries) == 1
        assert entries[0]["source"] == "list_sentry_uptime_alerts"
        assert entries[0]["summary"] == "3 uptime monitor(s), 1 down"

    def test_records_entry_without_down_clause_when_all_up(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_sentry_uptime_alerts(
            evidence,
            {
                "available": True,
                "monitor_count": 2,
                "down_count": 0,
                "monitors": [{"name": "api", "health": "ok"}],
            },
            {},
        )

        assert evidence["catalog_entries"][0]["summary"] == "2 uptime monitor(s)"

    def test_records_nothing_when_no_monitors(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_sentry_uptime_alerts(
            evidence, {"available": True, "monitor_count": 0, "monitors": []}, {}
        )

        assert "catalog_entries" not in evidence

    def test_records_nothing_on_unavailable_result(self) -> None:
        evidence: dict[str, Any] = {}

        _map_list_sentry_uptime_alerts(
            evidence, {"available": False, "error": "not configured"}, {}
        )

        assert "catalog_entries" not in evidence
