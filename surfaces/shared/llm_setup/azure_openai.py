"""Azure setup catalogs use resource deployments, not global API model names."""

from __future__ import annotations

from core.llm.providers.azure_openai import discover_azure_openai_deployments_from_env
from surfaces.shared.llm_setup.catalog import ProviderOption


def provider_models(_provider: ProviderOption) -> list[tuple[str, str]]:
    """Discover the configured resource's deployments during intentional setup."""
    return [(name, name) for name in discover_azure_openai_deployments_from_env()]
