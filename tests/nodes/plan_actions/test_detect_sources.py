from __future__ import annotations

from app.nodes.plan_actions.detect_sources import detect_sources


def test_detect_sources_includes_honeycomb_from_resolved_integrations() -> None:
    sources = detect_sources(
        raw_alert={
            "alert_source": "honeycomb",
            "annotations": {
                "service_name": "checkout-api",
                "trace_id": "trace-123",
                "summary": "checkout-api latency regression",
            },
        },
        context={},
        resolved_integrations={
            "honeycomb": {
                "api_key": "hny_test",
                "dataset": "prod-api",
                "base_url": "https://api.honeycomb.io",
            }
        },
    )

    assert sources["honeycomb"]["dataset"] == "prod-api"
    assert sources["honeycomb"]["service_name"] == "checkout-api"
    assert sources["honeycomb"]["trace_id"] == "trace-123"


def test_detect_sources_includes_coralogix_with_scoped_default_query() -> None:
    sources = detect_sources(
        raw_alert={
            "alert_source": "coralogix",
            "annotations": {
                "application_name": "payments",
                "subsystem_name": "worker",
                "summary": "payments worker timeout exceptions",
            },
        },
        context={},
        resolved_integrations={
            "coralogix": {
                "api_key": "cx_test",
                "base_url": "https://api.coralogix.com",
                "application_name": "payments",
                "subsystem_name": "worker",
            }
        },
    )

    assert sources["coralogix"]["application_name"] == "payments"
    assert sources["coralogix"]["subsystem_name"] == "worker"
    assert "$l.applicationname == 'payments'" in sources["coralogix"]["default_query"]
