"""Azure OpenAI provider helpers for LiteLLM routing and validation."""

from __future__ import annotations

import os
from typing import Any

from config.constants.llm import (
    AZURE_OPENAI_API_KEY_ENV,
    AZURE_OPENAI_API_VERSION_ENV,
    AZURE_OPENAI_BASE_URL_ENV,
)
from config.llm_auth.azure import normalize_azure_openai_base_url
from core.llm.types import ModelType

AZURE_OPENAI_PROVIDER = "azure-openai"


def is_azure_openai_provider(provider: str) -> bool:
    """Return whether *provider* is the Azure OpenAI LLM slug."""
    return provider.strip().lower() == AZURE_OPENAI_PROVIDER


def select_azure_openai_model(settings: Any, model_type: ModelType) -> str:
    """Return the configured Azure deployment name for *model_type*."""
    attr = f"azure_openai_{model_type}_model"
    return str(getattr(settings, attr))


def azure_openai_deployment_name(value: str) -> str:
    """Return the Azure deployment name without the LiteLLM ``azure/`` prefix."""
    name = value.strip()
    return name.removeprefix("azure/")


def azure_openai_litellm_model(deployment: str) -> str:
    """Build the LiteLLM model string for an Azure deployment name."""
    name = deployment.strip()
    if name.startswith("azure/"):
        return name
    return f"azure/{name}"


def is_azure_litellm_model(model: str) -> bool:
    """Return whether *model* is a LiteLLM Azure OpenAI model string."""
    return model.strip().startswith("azure/")


def resolve_azure_openai_api_version(value: str = "") -> str:
    """Return the configured Azure API version, falling back to the OpenSRE default."""
    from config.llm_models import DEFAULT_AZURE_OPENAI_API_VERSION

    version = (value or os.getenv(AZURE_OPENAI_API_VERSION_ENV, "")).strip()
    return version or DEFAULT_AZURE_OPENAI_API_VERSION


def azure_deployments_list_curl_command(*, api_version: str = "") -> str:
    """Return a shell command that lists deployments in the configured resource."""
    resolved_api_version = resolve_azure_openai_api_version(api_version)
    return (
        'curl -H "api-key: $AZURE_OPENAI_API_KEY" '
        f'"$AZURE_OPENAI_BASE_URL/openai/deployments?api-version={resolved_api_version}"'
    )


def is_azure_deployment_lookup_error(error: Exception) -> bool:
    """Return whether *error* likely indicates a missing Azure deployment."""
    message = str(error).lower()
    return "404" in message or (
        "not found" in message and ("deployment" in message or "azure" in message)
    )


def is_azure_openai_failure_message(message: str) -> bool:
    """Return whether a user-facing failure message refers to Azure deployments."""
    text = message.lower()
    return (
        "azure openai deployment" in text
        or "azure/gpt" in text
        or ("deployment" in text and "azure" in text)
    )


def format_azure_deployment_not_found_message(deployment: str) -> str:
    """Build guidance when an Azure deployment name cannot be resolved."""
    name = azure_openai_deployment_name(deployment)
    return (
        f"Azure OpenAI deployment '{name}' was not found. "
        "Set AZURE_OPENAI_*_MODEL to your deployment name from the Azure portal "
        "(not a model ID from /openai/models). "
        f"List deployments with: {azure_deployments_list_curl_command()}"
    )


def azure_deployment_not_found_remediation_steps() -> list[str]:
    """Return remediation steps for Azure deployment 404s."""
    return [
        (
            "Set AZURE_OPENAI_*_MODEL to your deployment name from the Azure portal, "
            + "not a model ID from /openai/models."
        ),
        f"List deployments: {azure_deployments_list_curl_command()}",
        "Create or rename the deployment in Azure AI Foundry if needed.",
    ]


def list_azure_openai_deployments(
    *,
    base_url: str,
    api_key: str,
    api_version: str = "",
) -> list[str]:
    """Return deployment IDs configured in an Azure OpenAI resource."""
    import httpx

    normalized_base = normalize_azure_openai_base_url(base_url)
    if not normalized_base or not api_key.strip():
        return []
    resolved_api_version = resolve_azure_openai_api_version(api_version)
    url = f"{normalized_base}/openai/deployments?api-version={resolved_api_version}"
    try:
        response = httpx.get(
            url,
            headers={"api-key": api_key.strip()},
            timeout=30.0,
        )
        response.raise_for_status()
        payload = response.json()
    except Exception:
        return []
    deployments: list[str] = []
    for item in payload.get("data", []):
        if not isinstance(item, dict):
            continue
        deployment_id = str(item.get("id", "")).strip()
        if deployment_id:
            deployments.append(deployment_id)
    return deployments


def discover_azure_openai_deployments_from_env() -> list[str]:
    """Return deployment IDs from the configured Azure OpenAI resource."""
    from config.llm_credentials import resolve_env_credential

    base_url = os.getenv(AZURE_OPENAI_BASE_URL_ENV, "")
    api_key = resolve_env_credential(AZURE_OPENAI_API_KEY_ENV) or ""
    return list_azure_openai_deployments(
        base_url=base_url,
        api_key=api_key,
        api_version=resolve_azure_openai_api_version(),
    )


def azure_openai_endpoint_configured() -> bool:
    """Return True when the Azure OpenAI resource URL is present."""
    base = os.getenv(AZURE_OPENAI_BASE_URL_ENV, "").strip()
    return bool(base)


def resolve_azure_openai_request_kwargs(settings: Any, *, model_type: ModelType) -> dict[str, str]:
    """Resolve LiteLLM request fields for Azure OpenAI from runtime settings."""
    base_url = normalize_azure_openai_base_url(str(getattr(settings, "azure_openai_base_url", "")))
    api_version = resolve_azure_openai_api_version(
        str(getattr(settings, "azure_openai_api_version", ""))
    )
    if not base_url:
        raise RuntimeError(
            f"LLM provider '{AZURE_OPENAI_PROVIDER}' requires {AZURE_OPENAI_BASE_URL_ENV}."
        )
    deployment = select_azure_openai_model(settings, model_type)
    return {
        "litellm_model": azure_openai_litellm_model(deployment),
        "api_base": base_url,
        "api_version": api_version,
        "api_key_env": AZURE_OPENAI_API_KEY_ENV,
    }


__all__ = [
    "AZURE_OPENAI_PROVIDER",
    "azure_deployment_not_found_remediation_steps",
    "azure_deployments_list_curl_command",
    "azure_openai_deployment_name",
    "azure_openai_endpoint_configured",
    "azure_openai_litellm_model",
    "discover_azure_openai_deployments_from_env",
    "format_azure_deployment_not_found_message",
    "is_azure_deployment_lookup_error",
    "is_azure_litellm_model",
    "is_azure_openai_failure_message",
    "is_azure_openai_provider",
    "list_azure_openai_deployments",
    "resolve_azure_openai_api_version",
    "resolve_azure_openai_request_kwargs",
    "select_azure_openai_model",
]
