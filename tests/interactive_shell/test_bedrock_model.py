"""Provider model validation accepts exact Bedrock profile IDs and ARNs."""

from __future__ import annotations

from dataclasses import dataclass

from surfaces.shared.llm_setup.selection import _is_model_allowed


@dataclass(frozen=True)
class _FakeModelOption:
    value: str
    label: str = ""


@dataclass(frozen=True)
class _FakeProvider:
    value: str
    models: tuple[_FakeModelOption, ...] = ()
    allow_custom_models: bool = False


class TestIsModelSupportedBedrock:
    """Bedrock must accept any non-empty model string (ARNs, regional prefixes, etc.)."""

    def test_bedrock_accepts_inference_profile_id(self) -> None:
        provider = _FakeProvider(value="bedrock", allow_custom_models=True)
        assert _is_model_allowed(provider, "us.anthropic.claude-sonnet-4-6") is True

    def test_bedrock_accepts_eu_prefix(self) -> None:
        provider = _FakeProvider(value="bedrock", allow_custom_models=True)
        assert _is_model_allowed(provider, "eu.anthropic.claude-sonnet-4-6") is True

    def test_bedrock_accepts_global_prefix(self) -> None:
        provider = _FakeProvider(value="bedrock", allow_custom_models=True)
        assert _is_model_allowed(provider, "global.anthropic.claude-opus-4-7") is True

    def test_bedrock_accepts_on_demand_model(self) -> None:
        provider = _FakeProvider(value="bedrock", allow_custom_models=True)
        assert _is_model_allowed(provider, "mistral.mistral-large-3-675b-instruct") is True

    def test_bedrock_accepts_full_arn(self) -> None:
        arn = "arn:aws:bedrock:us-east-1:123456789012:inference-profile/my-profile"
        provider = _FakeProvider(value="bedrock", allow_custom_models=True)
        assert _is_model_allowed(provider, arn) is True

    def test_bedrock_rejects_empty_string(self) -> None:
        provider = _FakeProvider(value="bedrock", allow_custom_models=True)
        assert _is_model_allowed(provider, "") is False

    def test_ollama_also_accepts_any_model(self) -> None:
        """Ollama shares the same bypass pattern — verify it still works."""
        provider = _FakeProvider(value="ollama", allow_custom_models=True)
        assert _is_model_allowed(provider, "llama3.2") is True

    def test_other_provider_requires_match(self) -> None:
        """Non-bypass providers must match the curated model list."""
        models = (_FakeModelOption(value="gpt-5.4"),)
        provider = _FakeProvider(value="anthropic", models=models)
        assert _is_model_allowed(provider, "gpt-5.4") is True
        assert _is_model_allowed(provider, "gpt-unknown") is False

    def test_custom_provider_accepts_unknown_non_empty_model(self) -> None:
        provider = _FakeProvider(value="openai", allow_custom_models=True)
        assert _is_model_allowed(provider, "gpt-5.5") is True
