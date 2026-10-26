from __future__ import annotations

from integrations._verifiers_loader import register_all_verifiers
from integrations.registry import (
    DIRECT_CLASSIFIED_EFFECTIVE_SERVICES,
    INTEGRATION_SPECS,
    SKIP_CLASSIFIED_SERVICES,
    SUPPORTED_SETUP_SERVICES,
    SUPPORTED_VERIFY_SERVICES,
    family_key,
    resolve_management_service,
    service_key,
)
from integrations.verification import list_verifiers

register_all_verifiers()


def test_registry_declares_each_service_once() -> None:
    services = [spec.service for spec in INTEGRATION_SPECS]
    assert len(services) == len(set(services))


def test_registry_supported_lists_are_derived_from_specs() -> None:
    # Lists, not tuples: the derived tables are mutated in place on registration
    # so that readers holding an imported reference keep seeing current data.
    expected_verify = [
        spec.service
        for spec in sorted(
            (candidate for candidate in INTEGRATION_SPECS if candidate.has_verifier),
            key=lambda candidate: (
                candidate.verify_order if candidate.verify_order is not None else 10_000
            ),
        )
    ]
    expected_setup = [
        spec.service
        for spec in sorted(
            (candidate for candidate in INTEGRATION_SPECS if candidate.setup_order is not None),
            key=lambda candidate: (
                candidate.setup_order if candidate.setup_order is not None else 10_000
            ),
        )
    ]

    assert expected_verify == SUPPORTED_VERIFY_SERVICES
    assert expected_setup == SUPPORTED_SETUP_SERVICES
    assert set(SUPPORTED_VERIFY_SERVICES).issubset(set(list_verifiers()))


def test_every_setup_spec_has_handler() -> None:
    # #2537: a spec with `setup_order` but no matching `_HANDLERS` entry lets
    # Click accept a service that cmd_setup cannot dispatch. Anchor the
    # inverse-drift here.
    from integrations.cli import _HANDLERS

    missing = [svc for svc in SUPPORTED_SETUP_SERVICES if svc not in _HANDLERS]
    assert not missing, (
        f"Registry declares setup_order for {missing} but no _HANDLERS entry "
        "in integrations/cli.py. These services are silently dropped from "
        "_SETUP_SERVICES, so `opensre integrations setup <svc>` will reject them "
        "with the 'Usage: setup <service>' error."
    )


def test_registry_preserves_aliases_and_special_case_buckets() -> None:
    assert service_key("github_mcp") == "github"
    assert service_key("carologix") == "coralogix"
    assert service_key("open search") == "opensearch"
    assert family_key("grafana_local") == "grafana"
    assert family_key("grafana") == "grafana"
    # Slack must classify (bot token / webhook) so teammate tools resolve —
    # it is not a skip_classification transport-only stub.
    assert "slack" not in SKIP_CLASSIFIED_SERVICES
    assert "slack" in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES
    assert "grafana" in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES
    assert "bitbucket" not in DIRECT_CLASSIFIED_EFFECTIVE_SERVICES


def test_railway_is_registered_as_a_configurable_verified_integration() -> None:
    railway = next(spec for spec in INTEGRATION_SPECS if spec.service == "railway")

    assert railway.has_verifier is True
    assert railway.direct_effective is True
    assert railway.setup_order is not None
    assert "railway" not in SKIP_CLASSIFIED_SERVICES


def test_google_docs_has_a_dedicated_setup_command() -> None:
    google_docs = next(spec for spec in INTEGRATION_SPECS if spec.service == "google_docs")

    assert google_docs.has_verifier is True
    assert google_docs.setup_order is not None
    assert "google_docs" in SUPPORTED_SETUP_SERVICES


def test_prefect_is_registered_as_a_directly_effective_integration() -> None:
    # prefect's classify() resolves store credentials into a config object
    # just like dagster/temporal, but its spec was missing direct_effective=True
    # — resolve_effective_integrations() only publishes services listed in
    # DIRECT_CLASSIFIED_EFFECTIVE_SERVICES, so a prefect integration saved in
    # the local store silently never resolved, breaking both
    # `opensre integrations verify prefect` and the prefect tools'
    # is_available() check for any store-configured instance.
    prefect = next(spec for spec in INTEGRATION_SPECS if spec.service == "prefect")

    assert prefect.has_verifier is True
    assert prefect.direct_effective is True
    assert "prefect" not in SKIP_CLASSIFIED_SERVICES


def test_resolve_management_service_keeps_posthog_and_posthog_mcp_distinct() -> None:
    # Like sentry / sentry_mcp, bare posthog is the REST integration and
    # posthog_mcp is the separate MCP flow — they must not alias each other.
    assert resolve_management_service("posthog") == "posthog"
    assert resolve_management_service("  PostHog  ") == "posthog"
    assert resolve_management_service("posthog_mcp") == "posthog_mcp"
    assert "posthog_mcp" in SUPPORTED_SETUP_SERVICES
    assert "posthog_mcp" in SUPPORTED_VERIFY_SERVICES
    assert "posthog" in SUPPORTED_VERIFY_SERVICES
    assert "posthog" in SUPPORTED_SETUP_SERVICES


def test_resolve_management_service_leaves_other_services_unaliased() -> None:
    # Unrelated services pass through, and `sentry` must NOT collapse into the
    # separate `sentry_mcp` flow.
    assert resolve_management_service("datadog") == "datadog"
    assert resolve_management_service("sentry") == "sentry"
    assert resolve_management_service("sentry_mcp") == "sentry_mcp"
    # Global registry aliases still resolve through the management path.
    assert resolve_management_service("github_mcp") == "github"
