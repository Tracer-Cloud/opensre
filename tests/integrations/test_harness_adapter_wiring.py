"""Every vendor extension point is actually wired at startup.

The refactor that moved vendor-specific behavior out of ``core/`` replaced
direct imports with registries: integrations register prompt fragments, anchor
parsers, alert-detail fields, and repo-scope providers from
``integrations/harness_adapters.py``. A vendor that stops registering does not
raise — it silently disappears from prompts and parsing. These tests fail
loudly instead, one assertion per registry.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

import infrastructure.harness_providers as harness_providers
from core.domain.alerts import extraction as alert_extraction
from core.domain.types import incident_anchors
from surfaces.shared.terminal.output import boundary as output_boundary


@pytest.fixture(autouse=True)
def _installed_ports() -> Iterator[None]:
    output_boundary.install_harness_providers()
    yield
    harness_providers.reset_harness_providers()


def test_action_prompt_fragments_cover_every_registered_vendor() -> None:
    # Act
    fragments = harness_providers.action_prompt_vendor_fragments()

    # Assert: GitHub routing plus the shared chat-delivery fallback; chat vendors'
    # own routing lives on their tool descriptions, not in every prompt.
    assert "github" in fragments.lower()
    assert "MESSAGING DELIVERY" in fragments
    assert "slack_read_messages" not in fragments


def test_assistant_prompt_fragments_cover_every_registered_vendor() -> None:
    # Act
    fragments = harness_providers.assistant_prompt_vendor_fragments()

    # Assert
    assert "sentry" in fragments.lower()
    assert "posthog" in fragments.lower()
    assert "slack" in fragments.lower()


def test_gateway_persona_fragment_is_registered() -> None:
    # Act
    persona = harness_providers.gateway_persona_fragments()

    # Assert: the Slack teammate persona wording core no longer owns.
    assert "AI production engineer" in persona
    assert "never call it the 'interactive shell'" in persona


def test_setupable_integration_services_come_from_cli_handlers() -> None:
    services = harness_providers.setupable_integration_services()
    assert "posthog_mcp" in services or "grafana" in services
    assert "mixpanel" not in services

    assert harness_providers.integration_setup_command("posthog_mcp") == (
        "/integrations setup posthog_mcp"
    )


def test_preferred_evidence_sources_opt_in_from_posthog_mcp_package() -> None:
    # Assert: PostHog MCP opts itself in; core still has no vendor list.
    assert harness_providers.preferred_evidence_sources_for("metric_read") == ("posthog_mcp",)
    assert harness_providers.preferred_evidence_sources_for("incident") == ()


def test_preferred_evidence_sources_are_additive_across_vendors() -> None:
    harness_providers.register_preferred_evidence_source("metric_read", "other_analytics")
    assert harness_providers.preferred_evidence_sources_for("metric_read") == (
        "posthog_mcp",
        "other_analytics",
    )


def test_omitting_vendor_registration_leaves_metric_read_unpreferred() -> None:
    """If no vendor opts in, metric asks must not invent a preferred source."""
    harness_providers.clear_preferred_evidence_sources()
    assert harness_providers.preferred_evidence_sources_for("metric_read") == ()


def test_message_context_stripper_handles_slack_prefix() -> None:
    # Arrange
    text = "[Slack channel_id=C123 thread_ts=1.2] who is on the team?"

    # Act
    prefix, remainder = harness_providers.strip_message_context_prefix(text)

    # Assert
    assert prefix.startswith("[Slack")
    assert remainder == "who is on the team?"


def test_vcs_repo_scope_providers_registered_for_both_hosts() -> None:
    # Assert: GitHub and GitLab both register a scope provider.
    assert len(harness_providers.repo_scope._vcs_repo_scope_providers) == 2


def test_incident_anchor_parsers_are_registered() -> None:
    # Assert: alertmanager, pagerduty, datadog, cloudwatch.
    assert len(incident_anchors._anchor_parsers) == 4


def test_alert_detail_fields_registered_for_aws_and_kubernetes() -> None:
    # Act
    fields = alert_extraction.alert_detail_field_names()

    # Assert
    for field in ("cloudwatch_log_group", "eks_cluster", "kube_namespace", "pod_name"):
        assert field in fields


def test_reinstalling_ports_does_not_duplicate_fragments() -> None:
    # Arrange: capture the single-install prompt text.
    once = harness_providers.action_prompt_vendor_fragments()

    # Act: startup wiring may run more than once (REPL + gateway in one process).
    output_boundary.install_harness_providers()
    twice = harness_providers.action_prompt_vendor_fragments()

    # Assert: each _register_* clears first, so text is not doubled.
    assert twice == once


def test_reset_clears_every_vendor_registry() -> None:
    # Act
    harness_providers.reset_harness_providers()

    # Assert: nothing vendor-specific survives into the next test.
    assert harness_providers.action_prompt_vendor_fragments() == ""
    assert harness_providers.assistant_prompt_vendor_fragments() == ""
    assert harness_providers.gateway_persona_fragments() == ""
    assert harness_providers.preferred_evidence_sources_for("metric_read") == ()
    assert harness_providers.repo_scope._vcs_repo_scope_providers == []
