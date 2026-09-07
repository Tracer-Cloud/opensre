"""Live credential check for Azure OpenAI deployments."""

from __future__ import annotations

from core.llm.providers.azure_openai import (
    format_azure_deployment_not_found_message,
    is_azure_deployment_lookup_error,
    list_azure_openai_deployments,
    normalize_azure_openai_base_url,
    resolve_azure_openai_api_version,
)
from surfaces.shared.llm_setup.openai_client import load_openai_client
from surfaces.shared.llm_setup.validation_result import ValidationResult


def format_validation_failure(
    *,
    deployment: str,
    base_url: str,
    api_key: str,
    api_version: str,
    error: Exception,
) -> str:
    """Explain Azure validation failures, listing deployments when possible."""
    if not is_azure_deployment_lookup_error(error):
        return f"Validation request failed: {error}"

    detail = format_azure_deployment_not_found_message(deployment)
    available = list_azure_openai_deployments(
        base_url=base_url,
        api_key=api_key,
        api_version=api_version,
    )
    if available:
        detail += f" Available deployments: {', '.join(available)}"
    return detail


def validate_credentials(
    *,
    api_key: str,
    deployment: str,
    base_url: str,
    api_version: str,
) -> ValidationResult:
    """Validate Azure OpenAI credentials with a tiny chat completion."""
    normalized_base = normalize_azure_openai_base_url(base_url)
    if not normalized_base:
        return ValidationResult(
            ok=False,
            detail="Azure OpenAI resource URL is missing. Set AZURE_OPENAI_BASE_URL.",
        )

    resolved_api_version = resolve_azure_openai_api_version(api_version)
    openai_client_cls, openai_auth_error = load_openai_client()
    azure_base = f"{normalized_base}/openai/deployments/{deployment}"
    try:
        client = openai_client_cls(
            api_key=api_key,
            base_url=azure_base,
            default_query={"api-version": resolved_api_version},
            timeout=30.0,
        )
        request_kwargs: dict[str, object] = {
            "model": deployment,
            "messages": [{"role": "user", "content": "Reply with exactly: OpenSRE ready"}],
        }
        if deployment.startswith(("o1", "o3", "o4", "gpt-5")):
            request_kwargs["max_completion_tokens"] = 24
        else:
            request_kwargs["max_tokens"] = 24
        response = client.chat.completions.create(**request_kwargs)
        sample_text = (response.choices[0].message.content or "").strip()
        return ValidationResult(
            ok=True,
            detail="Azure OpenAI API key validated.",
            sample_response=sample_text,
        )
    except openai_auth_error:
        return ValidationResult(ok=False, detail="Azure OpenAI rejected the API key.")
    except Exception as err:
        return ValidationResult(
            ok=False,
            detail=format_validation_failure(
                deployment=deployment,
                base_url=base_url,
                api_key=api_key,
                api_version=api_version,
                error=err,
            ),
        )


__all__ = ["format_validation_failure", "validate_credentials"]
