"""Wire integrations-layer helpers into :mod:`infrastructure.harness_providers`."""

from __future__ import annotations

from typing import Any


def _setupable_services() -> tuple[str, ...]:
    """Services with a setup wizard; imported on first use.

    ``integrations.cli`` pulls every vendor's setup flow (~2,000 modules).
    Registration must stay cheap at process boot, so the import waits until
    the port is actually read (``/integrations setup``).
    """
    from integrations.cli import setup_services

    return tuple(setup_services())


def _fetch_webapp_vault() -> list[dict[str, Any]] | None:
    """Webapp org integrations; the vault client imports on first use."""
    import integrations.webapp_vault as webapp_vault

    return webapp_vault.fetch_webapp_org_integrations()


def register_harness_adapters() -> None:
    from infrastructure.harness_providers import IntegrationResolutionAdapters
    from integrations.catalog import (
        classify_integrations,
        configured_integration_services,
        load_env_integrations,
        merge_integrations_by_service,
        merge_local_integrations,
    )
    from integrations.store import load_integrations, resolve_store_path

    IntegrationResolutionAdapters(
        load_integrations=load_integrations,
        integration_store_path=lambda: str(resolve_store_path()),
        load_env_integrations=load_env_integrations,
        classify_integrations=classify_integrations,
        merge_local_integrations=merge_local_integrations,
        merge_integrations_by_service=merge_integrations_by_service,
        configured_services=lambda: tuple(configured_integration_services()),
        setupable_services=_setupable_services,
        fetch_webapp_vault=_fetch_webapp_vault,
    ).install()

    _register_vcs_repo_scope_providers()
    _register_runbook_source_providers()
    _register_cli_llm_adapters()
    _register_alert_source_detectors()
    _register_alert_source_routing()
    _register_incident_anchor_parsers()
    _register_prompt_fragments()
    _register_message_context_strippers()
    _register_alert_detail_fields()
    _register_secondary_tool_sources()
    _register_gateway_persona()
    _register_preferred_evidence_sources()


def _register_vcs_repo_scope_providers() -> None:
    from infrastructure.harness_providers import (
        clear_vcs_repo_scope_providers,
        register_vcs_repo_scope_provider,
    )
    from integrations.github.repo_scope import GITHUB_VCS_REPO_SCOPE_PROVIDER
    from integrations.gitlab.repo_scope import GITLAB_VCS_REPO_SCOPE_PROVIDER

    clear_vcs_repo_scope_providers()
    register_vcs_repo_scope_provider(GITHUB_VCS_REPO_SCOPE_PROVIDER)
    register_vcs_repo_scope_provider(GITLAB_VCS_REPO_SCOPE_PROVIDER)


def _register_runbook_source_providers() -> None:
    from infrastructure.harness_providers import (
        clear_runbook_source_providers,
        register_runbook_source_provider,
    )
    from integrations.github.runbooks import build_github_runbook_source

    clear_runbook_source_providers()
    register_runbook_source_provider("github", build_github_runbook_source)


def _register_alert_source_detectors() -> None:
    from core.domain.alerts.alert_source import (
        clear_alert_source_detectors,
        register_alert_source_detector,
    )
    from integrations.grafana.alert_source_detect import detect_grafana_alert_source
    from integrations.yandex_cloud.alert_source_detect import detect_yandex_cloud_alert_source

    clear_alert_source_detectors()
    register_alert_source_detector(detect_grafana_alert_source)
    register_alert_source_detector(detect_yandex_cloud_alert_source)


def _register_alert_source_routing() -> None:
    from core.domain.alerts.alert_source import clear_alert_source_routing, clear_source_aliases
    from integrations.alert_source_catalog import register_all_alert_source_routing

    clear_alert_source_routing()
    clear_source_aliases()
    register_all_alert_source_routing()


def _register_incident_anchor_parsers() -> None:
    from core.domain.types.incident_anchors import (
        clear_anchor_parsers,
        register_anchor_parser,
    )
    from integrations.alertmanager.incident_anchor import alertmanager_incident_anchor
    from integrations.aws.cloudwatch_incident_anchor import cloudwatch_incident_anchor
    from integrations.datadog.incident_anchor import datadog_incident_anchor
    from integrations.pagerduty.incident_anchor import pagerduty_incident_anchor

    clear_anchor_parsers()
    # Order matters: the first parser to find an anchor wins. The order
    # reflects which format expresses incident-start most accurately.
    register_anchor_parser(alertmanager_incident_anchor)
    register_anchor_parser(pagerduty_incident_anchor)
    register_anchor_parser(datadog_incident_anchor)
    register_anchor_parser(cloudwatch_incident_anchor)


def _register_prompt_fragments() -> None:
    from infrastructure.harness_providers import (
        clear_action_prompt_fragments,
        clear_assistant_prompt_fragments,
        register_action_prompt_fragment,
        register_assistant_prompt_fragment,
    )
    from integrations.buzz.action_prompt import buzz_action_prompt_fragment
    from integrations.github.action_prompt import github_action_prompt_fragment
    from integrations.posthog.assistant_prompt import posthog_assistant_prompt_fragment
    from integrations.rocketchat.action_prompt import rocketchat_action_prompt_fragment
    from integrations.sentry.assistant_prompt import sentry_assistant_prompt_fragment
    from integrations.slack.action_prompt import slack_action_prompt_fragment
    from integrations.slack.assistant_prompt import slack_assistant_prompt_fragment
    from integrations.telegram.action_prompt import telegram_action_prompt_fragment

    clear_action_prompt_fragments()
    register_action_prompt_fragment(slack_action_prompt_fragment)
    register_action_prompt_fragment(github_action_prompt_fragment)
    register_action_prompt_fragment(telegram_action_prompt_fragment)
    register_action_prompt_fragment(rocketchat_action_prompt_fragment)
    register_action_prompt_fragment(buzz_action_prompt_fragment)

    clear_assistant_prompt_fragments()
    register_assistant_prompt_fragment(sentry_assistant_prompt_fragment)
    register_assistant_prompt_fragment(posthog_assistant_prompt_fragment)
    register_assistant_prompt_fragment(slack_assistant_prompt_fragment)


def _register_message_context_strippers() -> None:
    from infrastructure.harness_providers import (
        clear_message_context_prefix_strippers,
        register_message_context_prefix_stripper,
    )
    from integrations.slack.message_context import strip_slack_context_prefix

    clear_message_context_prefix_strippers()
    register_message_context_prefix_stripper(strip_slack_context_prefix)


def _register_alert_detail_fields() -> None:
    from core.domain.alerts.extraction import (
        clear_alert_detail_fields,
        register_alert_detail_fields,
    )
    from integrations.aws.alert_detail_fields import ALERT_DETAIL_FIELDS

    clear_alert_detail_fields()
    register_alert_detail_fields(*ALERT_DETAIL_FIELDS)


def _register_secondary_tool_sources() -> None:
    from core.domain.alerts.alert_source import (
        clear_secondary_tool_sources,
        register_secondary_tool_source,
    )

    clear_secondary_tool_sources()
    # Generic fallback sources: useful, but never primary when incident-specific
    # integrations match. Each is owned by its own integration package;
    # registered here (rather than from each package's own module-import time)
    # so the set is explicit and easy to audit in one place.
    for source in ("knowledge", "google_docs"):
        register_secondary_tool_source(source)


def _register_gateway_persona() -> None:
    from infrastructure.harness_providers import (
        clear_gateway_persona_fragments,
        register_gateway_persona_fragment,
    )
    from integrations.slack.gateway_persona import gateway_persona_prompt_fragment

    clear_gateway_persona_fragments()
    register_gateway_persona_fragment(gateway_persona_prompt_fragment)


def _register_preferred_evidence_sources() -> None:
    """Let vendor packages opt into ask kinds and unformed-metric draft fences.

    No central default list — each integration registers itself. Skip a
    vendor's ``register_*`` call to stop treating it as preferred (no L0 CTA /
    no dialect draft for that id).
    """
    from infrastructure.harness_providers import (
        clear_metric_query_drafts,
        clear_preferred_evidence_sources,
    )
    from integrations.grafana.metric_drafts import register_grafana_metric_drafts
    from integrations.posthog_mcp.evidence_sources import (
        register_posthog_mcp_evidence_sources,
    )
    from integrations.posthog_mcp.metric_drafts import register_posthog_mcp_metric_drafts

    clear_preferred_evidence_sources()
    clear_metric_query_drafts()
    register_posthog_mcp_evidence_sources()
    register_posthog_mcp_metric_drafts()
    register_grafana_metric_drafts()


def _register_cli_llm_adapters() -> None:

    from core.llm.types import CliLLMClient, ModelType
    from infrastructure.harness_providers import CliLlmAdapters
    from integrations.llm_cli.registry import get_cli_provider_registration
    from integrations.llm_cli.runner import CLIBackedLLMClient
    from integrations.llm_cli.text import flatten_messages_to_prompt

    def _build_cli_client(
        adapter: Any,
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        model_type: ModelType | None = None,
    ) -> CliLLMClient:
        kwargs: dict[str, Any] = {"model": model}
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if model_type is not None:
            kwargs["model_type"] = model_type
        return CLIBackedLLMClient(adapter, **kwargs)

    CliLlmAdapters(
        cli_provider_registration=get_cli_provider_registration,
        build_cli_client=_build_cli_client,
        flatten_cli_messages=flatten_messages_to_prompt,
    ).install()
