"""Unit tests for core.domain.alerts.alert_source."""

from __future__ import annotations

from core.domain.alerts.alert_source import (
    alert_source_routing,
    collect_alert_text,
    declared_context_sources,
    primary_sources_for_alert,
    relevant_sources_for_alert,
    resolve_alert_source,
    seed_tool_sources_for_alert,
)


def test_resolve_alert_source_from_state_field() -> None:
    assert resolve_alert_source({"alert_source": "Datadog"}) == "datadog"


def test_resolve_alert_source_from_raw_alert_field() -> None:
    assert resolve_alert_source({"raw_alert": {"alert_source": "Grafana"}}) == "grafana"


def test_resolve_alert_source_grafana_labels_heuristic() -> None:
    state = {
        "raw_alert": {
            "commonLabels": {"grafana_folder": "prod-alerts"},
        }
    }
    assert resolve_alert_source(state) == "grafana"


def test_resolve_alert_source_grafana_datasource_uid_heuristic() -> None:
    state = {
        "raw_alert": {
            "commonLabels": {"datasource_uid": "abc123"},
        }
    }
    assert resolve_alert_source(state) == "grafana"


def test_resolve_alert_source_grafana_external_url_heuristic() -> None:
    state = {
        "raw_alert": {
            "externalURL": "https://grafana.example.com/alerting",
        }
    }
    assert resolve_alert_source(state) == "grafana"


def test_resolve_alert_source_empty_when_unresolved() -> None:
    assert resolve_alert_source({}) == ""
    assert resolve_alert_source({"raw_alert": "not a dict"}) == ""


def test_primary_sources_for_alert_known_source() -> None:
    assert primary_sources_for_alert({"alert_source": "eks"}) == (
        "eks",
        "ec2",
        "cloudtrail",
        "kubernetes",
    )


def test_seed_tool_sources_for_alert_known_source() -> None:
    assert seed_tool_sources_for_alert({"alert_source": "eks"}) == ("eks", "kubernetes")


def test_routing_registry_entries_are_well_formed() -> None:
    for alert_source, routing in alert_source_routing().items():
        assert routing.relevance_tool_sources, alert_source
        assert routing.seed_tool_sources, alert_source
        # Seeding is a subset of relevance: anything auto-invoked pre-LLM
        # must also be prioritized during planning.
        assert set(routing.seed_tool_sources) <= set(routing.relevance_tool_sources), alert_source


def test_primary_sources_for_alert_unknown_source() -> None:
    assert primary_sources_for_alert({"alert_source": "unknown_vendor"}) == ()


def test_declared_context_sources_from_common_annotations() -> None:
    state = {
        "raw_alert": {
            "commonAnnotations": {"context_sources": "github, Datadog"},
        }
    }
    assert declared_context_sources(state) == {"github", "datadog"}


def test_declared_context_sources_from_annotations() -> None:
    state = {
        "raw_alert": {
            "annotations": {"context_sources": "datadog"},
        }
    }
    assert declared_context_sources(state) == {"datadog"}


def test_declared_context_sources_from_common_labels() -> None:
    state = {
        "raw_alert": {
            "commonLabels": {"context_sources": "sentry"},
        }
    }
    assert declared_context_sources(state) == {"sentry"}


def test_declared_context_sources_from_labels_fallback() -> None:
    state = {
        "raw_alert": {
            "labels": {"context_sources": "grafana"},
        }
    }
    assert declared_context_sources(state) == {"grafana"}


def test_declared_context_sources_empty_when_missing() -> None:
    assert declared_context_sources({}) == set()
    assert declared_context_sources({"raw_alert": {"title": "x"}}) == set()


def test_collect_alert_text_lowercases_and_joins_fields() -> None:
    state = {
        "alert_name": "HighLatency",
        "message": "API Slow",
        "raw_alert": {
            "title": "Checkout",
            "commonAnnotations": {"summary": "p99 spike"},
        },
        "problem_md": "# Problem",
    }
    text = collect_alert_text(state)
    assert "highlatency" in text
    assert "api slow" in text
    assert "checkout" in text
    assert "p99 spike" in text
    assert "problem" in text


def test_collect_alert_text_includes_raw_alert_title() -> None:
    text = collect_alert_text({"alert_name": "Latency", "raw_alert": {"title": "CheckoutPipeline"}})
    assert "checkoutpipeline" in text
    assert "latency" in text


def test_collect_alert_text_from_string_raw_alert() -> None:
    text = collect_alert_text({"raw_alert": "Lambda ERROR timeout"})
    assert "lambda error timeout" in text


def test_relevant_sources_prefers_declared_context_sources() -> None:
    state = {
        "raw_alert": {
            "commonAnnotations": {"context_sources": "github"},
        },
        "message": "datadog monitor fired",
    }
    assert relevant_sources_for_alert(state, ["github", "datadog", "knowledge"]) == ["github"]


def test_relevant_sources_matches_keywords_in_alert_text() -> None:
    state = {"message": "kubernetes pod crashloop in eks cluster"}
    matched = relevant_sources_for_alert(state, ["eks", "datadog", "knowledge"])
    assert matched == ["eks"]


def test_primary_sources_for_alert_new_relic() -> None:
    assert primary_sources_for_alert({"alert_source": "new_relic"}) == ("new_relic",)


def test_relevant_sources_matches_newrelic_url_in_alert_text() -> None:
    state = {"message": "condition fired, see https://one.newrelic.com/alerts/incidents/123"}
    matched = relevant_sources_for_alert(state, ["new_relic", "datadog", "knowledge"])
    assert matched == ["new_relic"]


def test_relevant_sources_excludes_secondary_sources() -> None:
    state = {"message": "need guidance"}
    assert relevant_sources_for_alert(state, ["knowledge", "google_docs"]) == []


def test_relevant_sources_empty_when_no_text_and_no_declared_sources() -> None:
    assert relevant_sources_for_alert({}, ["github", "datadog"]) == []


def test_seed_sources_narrower_than_relevance_sources_for_eks() -> None:
    """Regression guard: seeding stays narrower than relevance routing."""
    routing = alert_source_routing()["eks"]
    assert routing.seed_tool_sources == ("eks", "kubernetes")
    assert "ec2" in routing.relevance_tool_sources
    assert "ec2" not in routing.seed_tool_sources
