"""Tests for OpsGenie source detection in detect_sources."""

from __future__ import annotations

from app.nodes.plan_actions.detect_sources import detect_sources

_OPSGENIE_INTEGRATION = {
    "opsgenie": {
        "api_key": "og-key",
        "region": "us",
    }
}


def test_opsgenie_source_created_when_integration_configured() -> None:
    sources = detect_sources({}, {}, resolved_integrations=_OPSGENIE_INTEGRATION)
    assert "opsgenie" in sources
    assert sources["opsgenie"]["api_key"] == "og-key"
    assert sources["opsgenie"]["region"] == "us"
    assert sources["opsgenie"]["connection_verified"] is True


def test_opsgenie_source_picks_up_alert_id_from_annotations() -> None:
    raw_alert = {"annotations": {"opsgenie_alert_id": "a-123"}}
    sources = detect_sources(raw_alert, {}, resolved_integrations=_OPSGENIE_INTEGRATION)
    assert sources["opsgenie"]["alert_id"] == "a-123"


def test_opsgenie_source_picks_up_alert_id_from_top_level() -> None:
    raw_alert = {"opsgenie_alert_id": "a-456"}
    sources = detect_sources(raw_alert, {}, resolved_integrations=_OPSGENIE_INTEGRATION)
    assert sources["opsgenie"]["alert_id"] == "a-456"


def test_opsgenie_source_picks_up_query_from_annotations() -> None:
    raw_alert = {"annotations": {"opsgenie_query": "status=open tag=env:prod"}}
    sources = detect_sources(raw_alert, {}, resolved_integrations=_OPSGENIE_INTEGRATION)
    assert sources["opsgenie"]["query"] == "status=open tag=env:prod"


def test_opsgenie_source_falls_back_to_alert_name_for_query() -> None:
    raw_alert = {"alert_name": "HighCPU"}
    sources = detect_sources(raw_alert, {}, resolved_integrations=_OPSGENIE_INTEGRATION)
    assert sources["opsgenie"]["query"] == "HighCPU"


def test_opsgenie_source_not_created_without_integration() -> None:
    sources = detect_sources({}, {}, resolved_integrations={})
    assert "opsgenie" not in sources


def test_opsgenie_source_not_created_when_no_resolved_integrations() -> None:
    sources = detect_sources({}, {}, resolved_integrations=None)
    assert "opsgenie" not in sources


def test_opsgenie_source_not_created_when_api_key_missing() -> None:
    integrations = {"opsgenie": {"api_key": "", "region": "us"}}
    sources = detect_sources({}, {}, resolved_integrations=integrations)
    assert "opsgenie" not in sources


def test_opsgenie_source_eu_region() -> None:
    integrations = {"opsgenie": {"api_key": "k", "region": "eu"}}
    sources = detect_sources({}, {}, resolved_integrations=integrations)
    assert sources["opsgenie"]["region"] == "eu"
