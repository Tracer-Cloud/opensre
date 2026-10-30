"""Shared connection summaries and route-first login discovery."""

from surfaces.shared.llm_setup import auth_menu
from surfaces.shared.llm_setup.auth_profiles import iter_auth_profiles
from surfaces.shared.llm_setup.connection_summary import (
    summarize_connection,
    summarize_provider_connection,
)


def test_provider_routes_have_distinct_non_secret_summaries() -> None:
    profiles = {profile.name: profile for profile in iter_auth_profiles()}

    api = summarize_connection(profiles["openai"])
    codex = summarize_connection(profiles["codex"])

    assert api.route == "Direct API"
    assert api.billing == "API billing (usage-based)"
    assert api.authentication == "API key managed by OpenSRE"
    assert codex.route == "Vendor CLI"
    assert codex.billing.startswith("Billing unknown")
    assert codex.authentication.startswith("Vendor CLI")
    assert api.source_label != codex.source_label


def test_hosted_connection_is_described_separately_from_external_providers() -> None:
    from config.constants.llm import HOSTED_MODEL_PROVIDER

    summary = summarize_provider_connection(HOSTED_MODEL_PROVIDER)

    assert summary is not None
    assert summary.provider == "OpenSRE hosted"
    assert summary.billing == "OpenSRE credits"
    assert summary.authentication == "OpenSRE account session"


def test_login_picker_lists_all_routes_together_and_is_searchable(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def build_form(values, **kwargs):
        fields = kwargs["fields"](values)
        captured["field"] = fields[0]
        captured["heading"] = kwargs["heading"]
        return object()

    monkeypatch.setattr(auth_menu, "configured_profile_name", lambda: "openai")
    monkeypatch.setattr(auth_menu, "build_input_form", build_form)
    monkeypatch.setattr(auth_menu, "run_command_input", lambda _app: "codex")

    selected = auth_menu.prompt_auth_provider()
    field = captured["field"]

    assert selected is not None and selected.name == "codex"
    assert field.searchable
    routes = {choice.value for choice in field.choices}
    assert {"openai", "codex", "chatgpt"} <= routes
    codex_choice = next(choice for choice in field.choices if choice.value == "codex")
    assert "Billing unknown" in codex_choice.summary
    assert "Vendor CLI subprocess" in codex_choice.details
    assert captured["heading"] == "Available connections"
