from __future__ import annotations

from core.domain.runbooks import (
    IncidentIdentity,
    RunbookCatalogEntry,
    RunbookMatch,
    select_runbook,
)


def _entry(
    document_id: str,
    *,
    alertname: str = "",
    service: str = "",
    labels: tuple[tuple[str, str], ...] = (),
) -> RunbookCatalogEntry:
    return RunbookCatalogEntry(
        document_id=document_id,
        path=f"runbooks/{document_id}.md",
        match=RunbookMatch(alertname=alertname, service=service, labels=labels),
    )


def test_alertname_and_labels_take_precedence_over_alertname_only() -> None:
    incident = IncidentIdentity.from_values(
        alertname="CheckoutHighLatency",
        service="checkout",
        labels={"environment": "production", "region": "us-east-1"},
    )

    selection = select_runbook(
        (
            _entry("generic", alertname="CheckoutHighLatency"),
            _entry(
                "production",
                alertname="CheckoutHighLatency",
                labels=(("environment", "production"),),
            ),
        ),
        incident,
    )

    assert selection.status == "matched"
    assert selection.entry is not None
    assert selection.entry.document_id == "production"
    assert selection.reason == "alertname_labels"
    assert selection.matched_fields == ("alertname", "label:environment")


def test_more_specific_label_match_wins() -> None:
    incident = IncidentIdentity.from_values(
        alertname="CheckoutHighLatency",
        labels={"environment": "production", "region": "us-east-1"},
    )

    selection = select_runbook(
        (
            _entry(
                "production",
                alertname="CheckoutHighLatency",
                labels=(("environment", "production"),),
            ),
            _entry(
                "production-east",
                alertname="CheckoutHighLatency",
                labels=(("environment", "production"), ("region", "us-east-1")),
            ),
        ),
        incident,
    )

    assert selection.entry is not None
    assert selection.entry.document_id == "production-east"


def test_alertname_match_takes_precedence_over_service_match() -> None:
    incident = IncidentIdentity.from_values(
        alertname="CheckoutHighLatency",
        service="checkout",
    )

    selection = select_runbook(
        (
            _entry("service", service="checkout"),
            _entry("alert", alertname="CheckoutHighLatency"),
        ),
        incident,
    )

    assert selection.entry is not None
    assert selection.entry.document_id == "alert"
    assert selection.reason == "alertname"


def test_service_match_reports_labels_that_contributed_to_specificity() -> None:
    incident = IncidentIdentity.from_values(
        service="checkout",
        labels={"environment": "production"},
    )

    selection = select_runbook(
        (
            _entry(
                "production",
                service="checkout",
                labels=(("environment", "production"),),
            ),
        ),
        incident,
    )

    assert selection.status == "matched"
    assert selection.reason == "service"
    assert selection.matched_fields == ("service", "label:environment")


def test_non_matching_labels_disqualify_entry() -> None:
    incident = IncidentIdentity.from_values(
        alertname="CheckoutHighLatency",
        labels={"environment": "staging"},
    )

    selection = select_runbook(
        (
            _entry(
                "production",
                alertname="CheckoutHighLatency",
                labels=(("environment", "production"),),
            ),
            _entry("generic", alertname="CheckoutHighLatency"),
        ),
        incident,
    )

    assert selection.entry is not None
    assert selection.entry.document_id == "generic"


def test_equal_specificity_is_reported_as_ambiguous() -> None:
    incident = IncidentIdentity.from_values(alertname="CheckoutHighLatency")

    selection = select_runbook(
        (
            _entry("first", alertname="CheckoutHighLatency"),
            _entry("second", alertname="CheckoutHighLatency"),
        ),
        incident,
    )

    assert selection.status == "ambiguous"
    assert selection.entry is None
    assert selection.candidate_ids == ("first", "second")


def test_no_matching_entry_returns_not_found() -> None:
    selection = select_runbook(
        (_entry("database", service="database"),),
        IncidentIdentity.from_values(service="checkout"),
    )

    assert selection.status == "not_found"
    assert selection.entry is None
    assert selection.candidate_ids == ()
