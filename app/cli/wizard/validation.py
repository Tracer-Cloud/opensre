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


def validate_provider_credentials(
    *,
    provider: ProviderOption,
    api_key: str,
    model: str,
) -> ValidationResult:
    """Run a tiny live request against the selected provider."""
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
    from app.agent.tools.tool_actions.knowledge_sre_book.sre_knowledge_actions import (
        get_sre_guidance,
    )

    return get_sre_guidance(topic="recovery_remediation", max_topics=1)
