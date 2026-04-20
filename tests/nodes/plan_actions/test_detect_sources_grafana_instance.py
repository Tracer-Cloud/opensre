"""Tests for Grafana multi-instance selection in detect_sources.

When an alert payload carries a ``grafana_instance`` hint (top-level or
nested under ``annotations``), detect_sources must select that instance
from ``_all_grafana_instances``. When the hint is absent or unknown, it
falls back to the default (first) instance via ``resolved["grafana"]``.
"""

from __future__ import annotations

from app.nodes.plan_actions.detect_sources import detect_sources


def _multi_instance_resolved() -> dict:
    return {
        "grafana": {
            "endpoint": "https://prod.grafana.net",
            "api_key": "kp",
            "integration_id": "env-grafana",
        },
        "_all_grafana_instances": [
            {
                "name": "prod",
                "tags": {"env": "prod"},
                "config": {
                    "endpoint": "https://prod.grafana.net",
                    "api_key": "kp",
                    "integration_id": "env-grafana",
                },
                "integration_id": "env-grafana",
            },
            {
                "name": "staging",
                "tags": {"env": "staging"},
                "config": {
                    "endpoint": "https://staging.grafana.net",
                    "api_key": "ks",
                    "integration_id": "env-grafana",
                },
                "integration_id": "env-grafana",
            },
        ],
    }


def test_grafana_instance_hint_selects_named_instance() -> None:
    raw_alert = {"alert_source": "grafana", "grafana_instance": "staging"}
    sources = detect_sources(raw_alert, {}, resolved_integrations=_multi_instance_resolved())
    assert sources["grafana"]["grafana_endpoint"] == "https://staging.grafana.net"
    assert sources["grafana"]["grafana_api_key"] == "ks"


def test_no_hint_falls_back_to_default_instance() -> None:
    raw_alert = {"alert_source": "grafana"}
    sources = detect_sources(raw_alert, {}, resolved_integrations=_multi_instance_resolved())
    assert sources["grafana"]["grafana_endpoint"] == "https://prod.grafana.net"
    assert sources["grafana"]["grafana_api_key"] == "kp"


def test_unknown_hint_falls_back_to_default_instance() -> None:
    raw_alert = {"alert_source": "grafana", "grafana_instance": "qa-east"}
    sources = detect_sources(raw_alert, {}, resolved_integrations=_multi_instance_resolved())
    assert sources["grafana"]["grafana_endpoint"] == "https://prod.grafana.net"


def test_unknown_hint_logs_warning(caplog) -> None:
    """Operators should notice an unresolved hint (typo, removed instance)
    instead of silently querying the wrong Grafana."""
    import logging

    raw_alert = {"alert_source": "grafana", "grafana_instance": "qa-east"}
    with caplog.at_level(logging.WARNING, logger="app.nodes.plan_actions.detect_sources"):
        detect_sources(raw_alert, {}, resolved_integrations=_multi_instance_resolved())
    assert any("grafana_instance hint 'qa-east' not found" in r.message for r in caplog.records)


def test_hint_in_annotations_is_respected() -> None:
    raw_alert = {
        "alert_source": "grafana",
        "annotations": {"grafana_instance": "staging"},
    }
    sources = detect_sources(raw_alert, {}, resolved_integrations=_multi_instance_resolved())
    assert sources["grafana"]["grafana_endpoint"] == "https://staging.grafana.net"


def test_hint_normalized_to_lowercase() -> None:
    raw_alert = {"alert_source": "grafana", "grafana_instance": "STAGING"}
    sources = detect_sources(raw_alert, {}, resolved_integrations=_multi_instance_resolved())
    assert sources["grafana"]["grafana_endpoint"] == "https://staging.grafana.net"


def test_local_grafana_in_multi_instance_setup_discoverable_by_hint() -> None:
    """Regression: a local Grafana classified as grafana_local must still be
    findable in _all_grafana_instances when its sibling is a cloud instance."""
    resolved = {
        "grafana": {
            "endpoint": "https://prod.grafana.net",
            "api_key": "kp",
            "integration_id": "env-grafana",
        },
        "grafana_local": {
            "endpoint": "http://localhost:3000",
            "api_key": "",
            "integration_id": "env-grafana",
        },
        "_all_grafana_instances": [
            {
                "name": "local",
                "tags": {"env": "dev"},
                "config": {
                    "endpoint": "http://localhost:3000",
                    "api_key": "",
                    "integration_id": "env-grafana",
                },
                "integration_id": "env-grafana",
            },
            {
                "name": "prod",
                "tags": {"env": "prod"},
                "config": {
                    "endpoint": "https://prod.grafana.net",
                    "api_key": "kp",
                    "integration_id": "env-grafana",
                },
                "integration_id": "env-grafana",
            },
        ],
    }
    raw_alert = {"alert_source": "grafana", "grafana_instance": "local"}
    sources = detect_sources(raw_alert, {}, resolved_integrations=resolved)
    assert sources["grafana"]["grafana_endpoint"] == "http://localhost:3000"


def test_single_instance_setup_unchanged() -> None:
    """Backward compat: a resolved dict without _all_grafana_instances still
    routes to the single flat entry."""
    single = {
        "grafana": {
            "endpoint": "https://solo.grafana.net",
            "api_key": "solo",
            "integration_id": "g-solo",
        }
    }
    raw_alert = {"alert_source": "grafana"}
    sources = detect_sources(raw_alert, {}, resolved_integrations=single)
    assert sources["grafana"]["grafana_endpoint"] == "https://solo.grafana.net"
