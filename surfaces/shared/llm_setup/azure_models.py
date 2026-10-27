"""Azure deployment discovery; deployment IDs remain provider-owned."""

from __future__ import annotations

from core.llm.providers.azure_openai import discover_azure_openai_deployments_from_env
from surfaces.shared.llm_setup.catalog import ProviderOption


def discover_models(_provider: ProviderOption) -> tuple[tuple[str, str], ...]:
    return tuple((model, model) for model in discover_azure_openai_deployments_from_env())
