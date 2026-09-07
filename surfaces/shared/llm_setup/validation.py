"""Live provider validation and onboarding demo helpers."""

from __future__ import annotations

import os
from typing import Any

from surfaces.shared.llm_setup.catalog import ProviderOption
from surfaces.shared.llm_setup.openai_client import load_openai_client
from surfaces.shared.llm_setup.validation_result import ValidationResult

Anthropic: Any | None = None
AnthropicAuthError: type[Exception] | None = None


def _load_anthropic_client() -> tuple[Any, type[Exception]]:
    global Anthropic, AnthropicAuthError

    if Anthropic is None or AnthropicAuthError is None:
        from anthropic import Anthropic as _Anthropic
        from anthropic import AuthenticationError as _AnthropicAuthError

        Anthropic = _Anthropic
        AnthropicAuthError = _AnthropicAuthError

    return Anthropic, AnthropicAuthError


def _get_provider_base_url(provider_value: str) -> str | None:
    """Get the base_url for OpenAI-compatible non-OpenAI providers, or None for native OpenAI."""
    # Lazy imports keep config loading out of the validation module import graph.
    from config import llm_models

    # custom-openai's base URL is user-supplied: read + normalize it from the env
    # rather than a static default, so the probe hits the configured gateway.
    if provider_value == "custom-openai":
        from config.constants.llm import CUSTOM_OPENAI_BASE_URL_ENV, normalize_custom_base_url

        return normalize_custom_base_url(os.getenv(CUSTOM_OPENAI_BASE_URL_ENV, "")) or None
    base_urls = {
        "openrouter": llm_models.OPENROUTER_BASE_URL,
        "trustedrouter": llm_models.TRUSTEDROUTER_BASE_URL,
        "deepseek": llm_models.DEEPSEEK_BASE_URL,
        "gemini": llm_models.GEMINI_BASE_URL,
        "nvidia": llm_models.NVIDIA_BASE_URL,
        "groq": llm_models.GROQ_BASE_URL,
        "minimax": llm_models.MINIMAX_BASE_URL,
    }
    return base_urls.get(provider_value)


def _provider_validation_label(provider: ProviderOption) -> str:
    suffix = " API key"
    if provider.label.endswith(suffix):
        return provider.label[: -len(suffix)]
    return provider.label


def _check_ollama(host: str, model: str) -> ValidationResult:
    """Check Ollama server connectivity and verify model responds to inference."""
    import httpx

    from surfaces.shared.llm_setup.ollama import (
        OllamaModelDiscoveryError,
        list_available_models,
        model_is_available,
    )

    try:
        available = list_available_models(host)
    except OllamaModelDiscoveryError as exc:
        return ValidationResult(ok=False, detail=str(exc))
    if not model_is_available(model, available):
        listed = ", ".join(available) or "none pulled yet"
        return ValidationResult(
            ok=False,
            detail=f"Model '{model}' not found. Run: ollama pull {model}\nAvailable: {listed}",
        )
    # Verify the model actually responds to an inference request
    chat_url = f"{host.rstrip('/')}/v1/chat/completions"
    try:
        resp = httpx.post(
            chat_url,
            json={
                "model": model,
                "messages": [{"role": "user", "content": "Reply with exactly: OpenSRE ready"}],
                "max_tokens": 24,
            },
            timeout=60.0,
        )
        resp.raise_for_status()
        sample_text = (resp.json()["choices"][0]["message"]["content"] or "").strip()
    except Exception as err:
        return ValidationResult(
            ok=False,
            detail=f"Model '{model}' is pulled but failed to respond: {err}",
        )
    return ValidationResult(
        ok=True, detail=f"Ollama reachable. Model '{model}' is ready.", sample_response=sample_text
    )


def validate_provider_credentials(
    *,
    provider: ProviderOption,
    api_key: str,
    model: str,
) -> ValidationResult:
    """Run a tiny live request against the selected provider."""
    if provider.value == "ollama":
        return _check_ollama(host=api_key, model=model)

    if provider.value == "azure-openai":
        from surfaces.shared.llm_setup.azure_validation import (
            validate_credentials as validate_azure_credentials,
        )

        return validate_azure_credentials(
            api_key=api_key,
            deployment=model,
            base_url=os.getenv(provider.endpoint_env, "").strip(),
            api_version=os.getenv(provider.api_version_env, "").strip(),
        )

    anthropic_client_cls, anthropic_auth_error = _load_anthropic_client()
    openai_client_cls, openai_auth_error = load_openai_client()

    try:
        if provider.value in ("anthropic", "custom-anthropic"):
            anthropic_kwargs: dict[str, Any] = {"api_key": api_key, "timeout": 30.0}
            if provider.value == "custom-anthropic":
                # Point the probe at the user's gateway, not api.anthropic.com,
                # so a "validated" result reflects the endpoint the agent uses.
                from core.llm.providers.custom_endpoints import custom_anthropic_probe_base_url

                anthropic_base_url = custom_anthropic_probe_base_url()
                if anthropic_base_url:
                    anthropic_kwargs["base_url"] = anthropic_base_url
            anthropic_client = anthropic_client_cls(**anthropic_kwargs)
            anthropic_response = anthropic_client.messages.create(
                model=model,
                max_tokens=24,
                messages=[{"role": "user", "content": "Reply with exactly: OpenSRE ready"}],
            )
            sample_text = "".join(
                block.text
                for block in getattr(anthropic_response, "content", [])
                if getattr(block, "type", None) == "text"
            ).strip()
            return ValidationResult(
                ok=True,
                detail=f"{_provider_validation_label(provider)} API key validated.",
                sample_response=sample_text,
            )

        # All OpenAI-compatible providers (openai, openrouter, deepseek, gemini, nvidia,
        # groq, minimax) — a provider missing from _get_provider_base_url silently falls
        # back to api.openai.com and its (valid) key is reported as rejected.
        base_url = _get_provider_base_url(provider.value)
        openai_client = openai_client_cls(api_key=api_key, base_url=base_url, timeout=30.0)
        # Only native OpenAI reasoning models use max_completion_tokens; others use max_tokens
        if provider.value == "openai" and model.startswith(("o1", "o3", "o4", "gpt-5")):
            openai_response = openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: OpenSRE ready"}],
                max_completion_tokens=24,
            )
        else:
            openai_response = openai_client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": "Reply with exactly: OpenSRE ready"}],
                max_tokens=24,
            )
        sample_text = (openai_response.choices[0].message.content or "").strip()
        provider_label = _provider_validation_label(provider)
        return ValidationResult(
            ok=True, detail=f"{provider_label} API key validated.", sample_response=sample_text
        )
    except anthropic_auth_error:
        return ValidationResult(ok=False, detail="Anthropic rejected the API key.")
    except openai_auth_error:
        return ValidationResult(
            ok=False, detail=f"{_provider_validation_label(provider)} rejected the API key."
        )
    except Exception as err:
        return ValidationResult(ok=False, detail=f"Validation request failed: {err}")
