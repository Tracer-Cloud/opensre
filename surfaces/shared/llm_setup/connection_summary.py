"""Non-secret identity and route metadata shared by connection surfaces."""

from __future__ import annotations

from dataclasses import dataclass

from config.constants.llm import HOSTED_MODEL_PROVIDER
from surfaces.shared.llm_setup.auth_profiles import (
    ProviderAuthProfile,
    provider_for_profile,
    resolve_auth_profile,
)


@dataclass(frozen=True)
class ConnectionSummary:
    """Describe how one connection authenticates, executes, and is billed."""

    provider: str
    route: str
    account: str
    authentication: str
    execution: str
    billing: str

    @property
    def source_label(self) -> str:
        """Return the concise route and billing label used in model listings."""
        return f"{self.provider} · {self.route} · {self.billing}"

    @property
    def detail_lines(self) -> tuple[str, ...]:
        """Return safe account, authentication, execution, and billing details."""
        return (
            f"Account: {self.account}",
            f"Authentication: {self.authentication}",
            f"Execution: {self.execution}",
            f"Billing: {self.billing}",
        )


def summarize_connection(profile: ProviderAuthProfile) -> ConnectionSummary:
    """Describe a provider route without reading credentials or probing the provider."""
    provider = provider_for_profile(profile)
    if profile.kind == "api_key":
        custom_endpoint = provider.value in {"custom-openai", "custom-anthropic"}
        azure_endpoint = provider.value == "azure-openai"
        route = (
            "Custom API endpoint"
            if custom_endpoint
            else "Azure deployment"
            if azure_endpoint
            else "Direct API"
        )
        return ConnectionSummary(
            provider=provider.label,
            route=route,
            account=(
                "Configured endpoint; account details are not available"
                if custom_endpoint
                else "Configured Azure resource"
                if azure_endpoint
                else "Not available from API-key metadata"
            ),
            authentication="API key from OpenSRE credential storage or configured environment",
            execution=(
                "Configured API-compatible endpoint"
                if custom_endpoint
                else "Azure OpenAI endpoint"
                if azure_endpoint
                else "Native provider API"
            ),
            billing=(
                "Unknown (depends on the configured endpoint)"
                if custom_endpoint
                else "Azure usage billing"
                if azure_endpoint
                else "API billing (usage-based)"
            ),
        )
    if profile.kind == "subscription":
        return ConnectionSummary(
            provider=provider.label,
            route="Native subscription",
            account="Provider registration; account label is not shown here",
            authentication="Provider OAuth session",
            execution="Native provider API",
            billing="Provider subscription",
        )
    if profile.kind == "cli":
        return ConnectionSummary(
            provider=provider.label,
            route="Vendor CLI",
            account="Managed by the vendor CLI",
            authentication="Vendor CLI session or its configured credentials",
            execution="Vendor CLI subprocess",
            billing="Billing unknown (depends on vendor CLI configuration)",
        )
    if profile.kind == "host":
        return ConnectionSummary(
            provider=provider.label,
            route="Local endpoint",
            account="Configured local endpoint",
            authentication="Endpoint URL; no OpenSRE account identity",
            execution="Local server",
            billing="Local",
        )
    return ConnectionSummary(
        provider=provider.label,
        route="Cloud identity",
        account="Ambient cloud identity; account label is not resolved here",
        authentication="Cloud IAM / Application Default Credentials",
        execution="Native cloud-provider integration",
        billing="Cloud account usage",
    )


def summarize_provider_connection(provider: str) -> ConnectionSummary | None:
    """Resolve a provider route to its shared summary, including OpenSRE hosted access."""
    if provider == HOSTED_MODEL_PROVIDER:
        return ConnectionSummary(
            provider="OpenSRE hosted",
            route="Hosted gateway",
            account="OpenSRE account",
            authentication="OpenSRE account session",
            execution="OpenSRE hosted model service",
            billing="OpenSRE credits",
        )
    try:
        return summarize_connection(resolve_auth_profile(provider))
    except KeyError:
        return None


__all__ = [
    "ConnectionSummary",
    "summarize_connection",
    "summarize_provider_connection",
]
