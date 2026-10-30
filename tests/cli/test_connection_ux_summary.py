"""Shared connection summaries and route-first login discovery."""

from surfaces.shared.llm_setup.auth_profiles import iter_auth_profiles
from surfaces.shared.llm_setup.connection_summary import (
    summarize_connection,
    summarize_provider_connection,
)


def test_provider_routes_have_distinct_non_secret_summaries() -> None:
    profiles = {profile.name: profile for profile in iter_auth_profiles()}

    api = summarize_connection(profiles["openai"])
    codex = summarize_connection(profiles["codex"])
    custom = summarize_connection(profiles["custom-openai"])

    assert api.route == "Direct API"
    assert api.billing == "API billing (usage-based)"
    assert api.authentication == "API key managed by OpenSRE"
    assert codex.route == "Vendor CLI"
    assert codex.billing.startswith("Billing unknown")
    assert codex.authentication.startswith("Vendor CLI")
    assert api.source_label != codex.source_label
    assert custom.route == "Custom API endpoint"
    assert custom.billing.startswith("Unknown")


def test_hosted_connection_is_described_separately_from_external_providers() -> None:
    from config.constants.llm import HOSTED_MODEL_PROVIDER

    summary = summarize_provider_connection(HOSTED_MODEL_PROVIDER)

    assert summary is not None
    assert summary.provider == "OpenSRE hosted"
    assert summary.billing == "OpenSRE credits"
    assert summary.authentication == "OpenSRE account session"
