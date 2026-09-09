"""Agent-harness providers — integration, tool, and vendor behavior without tier violations.

The layer contract forbids ``core/agent_harness`` from importing ``integrations``
or ``tools``. Their behavior reaches the harness through the role-named modules
in this package: adapters register at process boot via
:func:`bootstrap.adapters.install_harness_adapters` (CLI, gateway, web, and
embedded profiles). Do not reintroduce a second registration site in a surface
or in :mod:`gateway.core.lifecycle.controller`.
"""

from __future__ import annotations

from infrastructure.harness_providers.cli_llm import (
    BuildCliClientFn,
    CliLlmAdapters,
    CliProviderRegistrationFn,
    FlattenCliMessagesFn,
    build_cli_client,
    cli_provider_registration,
    flatten_cli_messages_to_prompt,
)
from infrastructure.harness_providers.cli_llm import reset as _reset_cli_llm
from infrastructure.harness_providers.evidence_sources import (
    MetricCohortResolvedFn,
    MetricQueryDraft,
    clear_metric_query_drafts,
    clear_preferred_evidence_sources,
    metric_cohort_resolved_for,
    metric_query_draft_for,
    preferred_evidence_sources_by_kind,
    preferred_evidence_sources_for,
    register_discovery_targets,
    register_metric_cohort_resolver,
    register_metric_query_draft,
    register_metric_query_tools,
    register_preferred_evidence_source,
    registered_discovery_targets,
    registered_metric_query_tools,
)
from infrastructure.harness_providers.evidence_sources import reset as _reset_evidence_sources
from infrastructure.harness_providers.integration_resolution import (
    ClassifyIntegrationsFn,
    ConfiguredIntegrationServicesFn,
    IntegrationResolutionAdapters,
    IntegrationResolutionRequest,
    IntegrationResolutionResult,
    IntegrationSetupCommand,
    IntegrationStorePathFn,
    LoadEnvIntegrationsFn,
    LoadIntegrationsFn,
    MergeIntegrationsByServiceFn,
    MergeLocalIntegrationsFn,
    RemoteIntegrationsFetcher,
    RemoteIntegrationsProvider,
    SetupableIntegrationServicesFn,
    WebappVaultFetcherFn,
    configured_integration_services,
    fetch_remote_integrations,
    integration_setup_command,
    resolve_integrations,
    resolve_integrations_with_metadata,
    setupable_integration_services,
)
from infrastructure.harness_providers.integration_resolution import (
    reset as _reset_integration_resolution,
)
from infrastructure.harness_providers.message_context import (
    MessageContextPrefixStripper,
    clear_message_context_prefix_strippers,
    register_message_context_prefix_stripper,
    strip_message_context_prefix,
)
from infrastructure.harness_providers.message_context import reset as _reset_message_context
from infrastructure.harness_providers.prompt_fragments import (
    PromptFragmentFn,
    action_prompt_vendor_fragments,
    assistant_prompt_vendor_fragments,
    clear_action_prompt_fragments,
    clear_assistant_prompt_fragments,
    clear_gateway_persona_fragments,
    gateway_persona_fragments,
    register_action_prompt_fragment,
    register_assistant_prompt_fragment,
    register_gateway_persona_fragment,
)
from infrastructure.harness_providers.prompt_fragments import reset as _reset_prompt_fragments
from infrastructure.harness_providers.repo_scope import (
    VcsRepoScopeProvider,
    clear_vcs_repo_scope_providers,
    enrich_resolved_with_repo_scopes,
    register_vcs_repo_scope_provider,
)
from infrastructure.harness_providers.repo_scope import reset as _reset_repo_scope
from infrastructure.harness_providers.runbooks import (
    RunbookSourceFactory,
    clear_runbook_source_providers,
    register_runbook_source_provider,
    resolve_runbook_source,
)
from infrastructure.harness_providers.runbooks import reset as _reset_runbooks
from infrastructure.harness_providers.subprocess_presenter import (
    SubprocessPresenterProvider,
    resolve_subprocess_presenter,
)
from infrastructure.harness_providers.subprocess_presenter import (
    reset as _reset_subprocess_presenter,
)
from infrastructure.harness_providers.tool_registry import (
    ToolSources,
    resolve_surface_tool_map,
    resolve_surface_tools,
)
from infrastructure.harness_providers.tool_registry import reset as _reset_tool_registry


def reset_harness_providers() -> None:
    """Restore all harness providers to noop defaults (tests).

    Also clears core leaf registries that integrations register through
    :func:`integrations.harness_adapters.register_harness_adapters` (alert
    routing, anchor parsers, detail fields, …). Without
    those clears, tests that call ``reset_harness_providers()`` mid-suite would
    keep stale vendor registrations while VCS/prompt providers look empty — a
    silent inconsistency.
    """
    _reset_integration_resolution()
    _reset_tool_registry()
    _reset_cli_llm()
    _reset_repo_scope()
    _reset_runbooks()
    _reset_prompt_fragments()
    _reset_message_context()
    _reset_evidence_sources()
    _reset_subprocess_presenter()

    # Core leaf registries (populated by integrations/harness_adapters).
    from core.domain.alerts.alert_source import (
        clear_alert_source_detectors,
        clear_alert_source_routing,
        clear_secondary_tool_sources,
        clear_source_aliases,
    )
    from core.domain.alerts.extraction import clear_alert_detail_fields
    from core.domain.types.incident_anchors import clear_anchor_parsers

    clear_alert_source_detectors()
    clear_alert_source_routing()
    clear_source_aliases()
    clear_secondary_tool_sources()
    clear_alert_detail_fields()
    clear_anchor_parsers()


__all__ = [
    "BuildCliClientFn",
    "CliLlmAdapters",
    "ClassifyIntegrationsFn",
    "CliProviderRegistrationFn",
    "ConfiguredIntegrationServicesFn",
    "FlattenCliMessagesFn",
    "IntegrationResolutionAdapters",
    "IntegrationResolutionRequest",
    "IntegrationResolutionResult",
    "IntegrationSetupCommand",
    "IntegrationStorePathFn",
    "LoadEnvIntegrationsFn",
    "LoadIntegrationsFn",
    "MergeIntegrationsByServiceFn",
    "MergeLocalIntegrationsFn",
    "MessageContextPrefixStripper",
    "MetricCohortResolvedFn",
    "MetricQueryDraft",
    "PromptFragmentFn",
    "RemoteIntegrationsFetcher",
    "RemoteIntegrationsProvider",
    "RunbookSourceFactory",
    "SetupableIntegrationServicesFn",
    "SubprocessPresenterProvider",
    "ToolSources",
    "VcsRepoScopeProvider",
    "WebappVaultFetcherFn",
    "action_prompt_vendor_fragments",
    "assistant_prompt_vendor_fragments",
    "build_cli_client",
    "clear_action_prompt_fragments",
    "clear_assistant_prompt_fragments",
    "clear_gateway_persona_fragments",
    "clear_message_context_prefix_strippers",
    "clear_metric_query_drafts",
    "clear_preferred_evidence_sources",
    "clear_runbook_source_providers",
    "clear_vcs_repo_scope_providers",
    "cli_provider_registration",
    "configured_integration_services",
    "enrich_resolved_with_repo_scopes",
    "fetch_remote_integrations",
    "flatten_cli_messages_to_prompt",
    "gateway_persona_fragments",
    "integration_setup_command",
    "metric_cohort_resolved_for",
    "metric_query_draft_for",
    "preferred_evidence_sources_by_kind",
    "preferred_evidence_sources_for",
    "register_action_prompt_fragment",
    "register_assistant_prompt_fragment",
    "register_discovery_targets",
    "register_gateway_persona_fragment",
    "register_message_context_prefix_stripper",
    "register_metric_cohort_resolver",
    "register_metric_query_draft",
    "register_metric_query_tools",
    "register_preferred_evidence_source",
    "register_runbook_source_provider",
    "register_vcs_repo_scope_provider",
    "registered_discovery_targets",
    "registered_metric_query_tools",
    "reset_harness_providers",
    "resolve_integrations",
    "resolve_integrations_with_metadata",
    "resolve_runbook_source",
    "resolve_subprocess_presenter",
    "resolve_surface_tool_map",
    "resolve_surface_tools",
    "setupable_integration_services",
    "strip_message_context_prefix",
]
