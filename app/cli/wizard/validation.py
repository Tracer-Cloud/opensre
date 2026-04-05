"""Live provider validation and onboarding demo helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.cli.wizard.config import ProviderOption

Anthropic: Any | None = None
AnthropicAuthError: type[Exception] | None = None
OpenAI: Any | None = None
OpenAIAuthError: type[Exception] | None = None


def _load_anthropic_client() -> tuple[Any, type[Exception]]:
    global Anthropic, AnthropicAuthError

    if Anthropic is None or AnthropicAuthError is None:
        from anthropic import Anthropic as _Anthropic
        from anthropic import AuthenticationError as _AnthropicAuthError

        Anthropic = _Anthropic
        AnthropicAuthError = _AnthropicAuthError

    return Anthropic, AnthropicAuthError


def _load_openai_client() -> tuple[Any, type[Exception]]:
    global OpenAI, OpenAIAuthError

    if OpenAI is None or OpenAIAuthError is None:
        from openai import AuthenticationError as _OpenAIAuthError
        from openai import OpenAI as _OpenAI

        OpenAI = _OpenAI
        OpenAIAuthError = _OpenAIAuthError

    return OpenAI, OpenAIAuthError


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a provider key."""

    ok: bool
    detail: str
    sample_response: str = ""


def _get_provider_base_url(provider_value: str) -> str | None:
    """Get the base_url for OpenAI-compatible non-OpenAI providers, or None for native OpenAI."""
    if provider_value == "openrouter":
        from app.config import OPENROUTER_BASE_URL

        return OPENROUTER_BASE_URL
    if provider_value == "gemini":
        from app.config import GEMINI_BASE_URL

        return GEMINI_BASE_URL
    if provider_value == "nvidia":
        from app.config import NVIDIA_BASE_URL

        return NVIDIA_BASE_URL
    return None


def _check_ollama(host: str, model: str) -> ValidationResult:
    """Check Ollama server connectivity and verify model responds to inference."""
    import httpx

    tags_url = f"{host.rstrip('/')}/api/tags"
    try:
        r = httpx.get(tags_url, timeout=5.0)
        r.raise_for_status()
    except Exception as err:
        return ValidationResult(
            ok=False,
            detail=f"Cannot reach Ollama at {host}. Is it running? Try: ollama serve\n({err})",
        )
    available = [m["name"] for m in r.json().get("models", [])]
    if model not in available:
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
    return ValidationResult(ok=True, detail=f"Ollama reachable. Model '{model}' is ready.", sample_response=sample_text)


def validate_provider_credentials(
    *,
    provider: ProviderOption,
    api_key: str,
    model: str,
) -> ValidationResult:
    """Run a tiny live request against the selected provider."""
    if provider.value == "ollama":
        return _check_ollama(host=api_key, model=model)

    anthropic_client_cls, anthropic_auth_error = _load_anthropic_client()
    openai_client_cls, openai_auth_error = _load_openai_client()

    try:
        if provider.value == "anthropic":
            anthropic_client = anthropic_client_cls(api_key=api_key, timeout=30.0)
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
            return ValidationResult(ok=True, detail="Anthropic API key validated.", sample_response=sample_text)

        # All OpenAI-compatible providers (openai, openrouter, gemini, nvidia)
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
        return ValidationResult(ok=True, detail=f"{provider.label} API key validated.", sample_response=sample_text)
    except anthropic_auth_error:
        return ValidationResult(ok=False, detail="Anthropic rejected the API key.")
    except openai_auth_error:
        return ValidationResult(ok=False, detail=f"{provider.label} rejected the API key.")
    except Exception as err:
        return ValidationResult(ok=False, detail=f"Validation request failed: {err}")


def build_demo_action_response() -> dict:
    """Return a safe built-in action response for onboarding."""
    from app.tools.SREGuidanceTool import get_sre_guidance

    return get_sre_guidance(topic="recovery_remediation", max_topics=1)
