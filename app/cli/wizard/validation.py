"""Live provider validation and onboarding demo helpers."""

from __future__ import annotations

from dataclasses import dataclass

from anthropic import Anthropic
from anthropic import AuthenticationError as AnthropicAuthError
from openai import AuthenticationError as OpenAIAuthError
from openai import OpenAI

from app.agent.tools.tool_actions.knowledge_sre_book.sre_knowledge_actions import (
    get_sre_guidance,
)
from app.cli.wizard.config import ProviderOption


@dataclass(frozen=True)
class ValidationResult:
    """Result of validating a provider key."""

    ok: bool
    detail: str
    sample_response: str = ""


def validate_provider_credentials(
    *,
    provider: ProviderOption,
    api_key: str,
    model: str,
) -> ValidationResult:
    """Run a tiny live request against the selected provider."""
    try:
        if provider.value == "anthropic":
            client = Anthropic(api_key=api_key, timeout=30.0)
            response = client.messages.create(
                model=model,
                max_tokens=24,
                messages=[{"role": "user", "content": "Reply with exactly: OpenSRE ready"}],
            )
            sample_text = "".join(
                block.text for block in getattr(response, "content", []) if getattr(block, "type", None) == "text"
            ).strip()
            return ValidationResult(ok=True, detail="Anthropic API key validated.", sample_response=sample_text)

        client = OpenAI(api_key=api_key, timeout=30.0)
        request_kwargs = {
            "model": model,
            "messages": [{"role": "user", "content": "Reply with exactly: OpenSRE ready"}],
        }
        if model.startswith(("o1", "o3", "o4", "gpt-5")):
            request_kwargs["max_completion_tokens"] = 24
        else:
            request_kwargs["max_tokens"] = 24
        response = client.chat.completions.create(**request_kwargs)
        sample_text = (response.choices[0].message.content or "").strip()
        return ValidationResult(ok=True, detail="OpenAI API key validated.", sample_response=sample_text)
    except AnthropicAuthError:
        return ValidationResult(ok=False, detail="Anthropic rejected the API key.")
    except OpenAIAuthError:
        return ValidationResult(ok=False, detail="OpenAI rejected the API key.")
    except Exception as err:
        return ValidationResult(ok=False, detail=f"Validation request failed: {err}")


def build_demo_action_response() -> dict:
    """Return a safe built-in action response for onboarding."""
    return get_sre_guidance(topic="recovery_remediation", max_topics=1)
