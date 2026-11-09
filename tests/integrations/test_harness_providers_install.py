"""Integration tests for CLI → harness-provider install."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import infrastructure.harness_providers as harness_providers
from infrastructure.observability import NoopProgressTracker
from infrastructure.observability.render import debug as obs_debug
from infrastructure.observability.render import progress as obs_progress
from infrastructure.observability.render.debug import set_debug_printer
from infrastructure.observability.render.progress import (
    set_progress_tracker,
    set_progress_tracker_factory,
)
from integrations.tracer.integrations_adapter import fetch_tracer_remote_integrations
from surfaces.shared.terminal.output import boundary as output_boundary


def _reset_all_providers() -> None:
    harness_providers.reset_harness_providers()
    set_progress_tracker(NoopProgressTracker())
    set_progress_tracker_factory(None)
    obs_progress._silenced = False
    set_debug_printer(obs_debug._default_debug_printer)


@pytest.fixture(autouse=True)
def _reset_integrations_providers() -> Iterator[None]:
    _reset_all_providers()
    yield
    _reset_all_providers()


def test_remote_fetch_defaults_to_empty_before_boundary_install() -> None:
    assert harness_providers.fetch_remote_integrations(org_id="org-1", auth_token="tok") == []


def test_install_product_adapters_wires_tracer_fetcher() -> None:
    output_boundary.install_product_adapters()

    installed_remote = harness_providers.integration_resolution._installed_remote
    assert installed_remote is not None
    assert installed_remote.fetch is fetch_tracer_remote_integrations


def test_registered_fetcher_is_invoked() -> None:
    calls: list[tuple[str, str]] = []

    def _fake_fetcher(org_id: str, auth_token: str) -> list[dict[str, object]]:
        calls.append((org_id, auth_token))
        return [{"service": "grafana", "config": {}}]

    harness_providers.RemoteIntegrationsProvider(_fake_fetcher).install()
    result = harness_providers.fetch_remote_integrations(org_id="org-42", auth_token="jwt-here")

    assert calls == [("org-42", "jwt-here")]
    assert result == [{"service": "grafana", "config": {}}]


def test_reset_restores_webapp_vault_fetcher_default() -> None:
    # Arrange: register a distinctive fetcher that must not survive a reset —
    # if it leaks, every later test sees this instead of the noop default.
    def _sentinel_vault() -> list[dict[str, object]]:
        return [{"service": "leaked-vault-marker"}]

    harness_providers.IntegrationResolutionAdapters(fetch_webapp_vault=_sentinel_vault).install()
    assert (
        harness_providers.integration_resolution._adapters().fetch_webapp_vault is _sentinel_vault
    )

    # Act
    harness_providers.reset_harness_providers()

    # Assert: the noop default is restored, not the leaked sentinel.
    assert (
        harness_providers.integration_resolution._adapters().fetch_webapp_vault
        is harness_providers.integration_resolution._default_fetch_webapp_vault
    )


def test_install_harness_providers_wires_catalog_and_registry() -> None:
    output_boundary.install_harness_providers()

    assert (
        harness_providers.integration_resolution._adapters().load_integrations
        is not harness_providers.integration_resolution._default_load_integrations
    )
    assert isinstance(harness_providers.resolve_surface_tools("action"), list)


def test_install_harness_providers_wires_cli_llm_adapters() -> None:
    # Before install, the CLI-LLM backend fails loudly instead of silently no-op'ing.
    harness_providers.reset_harness_providers()
    with pytest.raises(RuntimeError, match="not registered"):
        harness_providers.build_cli_client(object(), model="x")

    output_boundary.install_harness_providers()

    installed = harness_providers.cli_llm._installed
    assert installed is not None
    assert (
        installed.cli_provider_registration
        is not harness_providers.cli_llm._default_cli_provider_registration
    )
    assert installed.build_cli_client is not harness_providers.cli_llm._cli_llm_backend_unavailable
    assert (
        installed.flatten_cli_messages is not harness_providers.cli_llm._cli_llm_backend_unavailable
    )


def test_install_harness_providers_wires_soc_registries() -> None:
    """SoC registries must not stay silently empty after install (or leak after reset).

    Alert routing, VCS scope, prompt fragments, Slack prefix
    stripping, secondary sources, and alert-detail fields all moved out of
    core into registered adapters. Empty registries look like "healthy but
    dumb" product behavior — this test fails loud if install regresses.
    """
    from core.agent_harness.prompts import build_action_system_prompt
    from core.agent_harness.turns.turn_snapshot import TurnSnapshot
    from core.domain.alerts.alert_source import (
        alert_source_routing,
        secondary_tool_sources,
        seed_tool_sources_for_alert,
    )
    from core.domain.alerts.extraction import alert_detail_field_names

    harness_providers.reset_harness_providers()

    assert alert_source_routing() == {}
    assert secondary_tool_sources() == frozenset()
    assert "kube_namespace" not in alert_detail_field_names()
    assert harness_providers.action_prompt_vendor_fragments() == ""
    assert harness_providers.gateway_persona_fragments() == ""
    prefix, remainder = harness_providers.strip_message_context_prefix("[Slack channel_id=C1]\nyes")
    assert prefix == ""
    assert remainder.startswith("[Slack")

    output_boundary.install_harness_providers()

    assert "grafana" in alert_source_routing()
    assert seed_tool_sources_for_alert({"alert_source": "grafana"}) == ("grafana",)
    assert "knowledge" in secondary_tool_sources()
    assert "kube_namespace" in alert_detail_field_names()
    fragments = harness_providers.action_prompt_vendor_fragments()
    assert "MESSAGING DELIVERY" in fragments
    assert "slack_send_message" not in fragments
    assert "telegram_send_message" not in fragments
    assert "colleague in Slack" in harness_providers.gateway_persona_fragments()
    prefix, remainder = harness_providers.strip_message_context_prefix("[Slack channel_id=C1]\nyes")
    assert prefix.startswith("[Slack")
    assert remainder.strip() == "yes"

    enriched = harness_providers.enrich_resolved_with_repo_scopes(
        resolved={"github": {"connection_verified": True}},
        message="https://github.com/Tracer-Cloud/opensre",
        conversation_messages=None,
        env={},
        cwd=None,
        cached_scopes={},
    )
    assert enriched["github"]["owner"] == "Tracer-Cloud"
    assert enriched["github"]["repo"] == "opensre"

    action_prompt = build_action_system_prompt(
        TurnSnapshot(
            text="",
            conversation_messages=(),
            configured_integrations=(),
            configured_integrations_known=False,
            reasoning_effort=None,
        )
    )
    assert "MESSAGING DELIVERY" in action_prompt
    assert "slack_send_message" not in action_prompt
    assert "GITHUB CLI REQUESTS" in action_prompt

    harness_providers.reset_harness_providers()
    assert alert_source_routing() == {}
    assert harness_providers.action_prompt_vendor_fragments() == ""
