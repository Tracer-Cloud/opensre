"""Regression tests for wizard path constants."""

from __future__ import annotations

import pytest

from surfaces.interactive_shell.command_registry.model.switching import _is_model_allowed
from surfaces.shared.llm_setup.catalog import (
    ANTHROPIC_MODELS,
    CLAUDE_CODE_MODELS,
    PROJECT_ENV_PATH,
    PROJECT_ROOT,
    PROVIDER_BY_VALUE,
    SUPPORTED_PROVIDERS,
)


def test_project_env_path_defaults_to_repo_root() -> None:
    """PROJECT_ROOT must resolve to the checkout root, not its parent directory."""
    assert (PROJECT_ROOT / "pyproject.toml").is_file()
    assert PROJECT_ENV_PATH == PROJECT_ROOT / ".env"


def test_provider_catalog_keeps_legacy_cli_providers_registered() -> None:
    """Legacy CLI provider values remain registered for existing configs."""
    labels_by_value = {provider.value: provider.label for provider in SUPPORTED_PROVIDERS}
    values = [provider.value for provider in SUPPORTED_PROVIDERS]

    assert labels_by_value["anthropic"] == "Anthropic API key"
    assert labels_by_value["claude-code"] == "Anthropic Claude Code CLI"
    assert values.index("anthropic") < values.index("claude-code") < values.index("openai")

    assert labels_by_value["openai"] == "OpenAI API key"
    assert labels_by_value["codex"] == "OpenAI Codex CLI"
    assert values.index("openai") < values.index("codex") < values.index("openrouter")

    assert labels_by_value["gemini"] == "Google Gemini API key"
    assert labels_by_value["gemini-cli"] == "Google Gemini CLI"
    assert labels_by_value["antigravity-cli"] == "Google Antigravity CLI"
    assert (
        values.index("gemini")
        < values.index("gemini-cli")
        < values.index("antigravity-cli")
        < values.index("nvidia")
    )

    assert labels_by_value["groq"] == "Groq API key"
    assert labels_by_value["grok-cli"] == "xAI Grok Build CLI"
    assert values.index("groq") < values.index("grok-cli") < values.index("cursor")


def test_claude_fable_5_is_selectable_without_custom_models() -> None:
    """#3621: anthropic keeps a curated list, so Fable 5 must be in it explicitly."""
    anthropic_values = [model.value for model in ANTHROPIC_MODELS]
    claude_code_values = [model.value for model in CLAUDE_CODE_MODELS]

    assert "claude-fable-5" in anthropic_values
    assert "claude-fable-5" in claude_code_values
    # Defaults stay unchanged: Fable 5 is pricier and opt-in only.
    assert anthropic_values[0] != "claude-fable-5"
    assert claude_code_values[0] == ""


@pytest.mark.parametrize(
    "model_id",
    ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"),
)
def test_new_claude_models_are_selectable_without_custom_models(model_id: str) -> None:
    anthropic_values = [model.value for model in ANTHROPIC_MODELS]
    claude_code_values = [model.value for model in CLAUDE_CODE_MODELS]

    assert model_id in anthropic_values
    assert model_id in claude_code_values
    assert anthropic_values[0] != model_id
    assert claude_code_values[0] == ""


def test_new_claude_models_allowed_by_anthropic_provider() -> None:
    provider = PROVIDER_BY_VALUE["anthropic"]
    assert provider.allow_custom_models is False
    assert provider.default_model == "claude-opus-4-7"

    for model_id in ("claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"):
        assert _is_model_allowed(provider, model_id) is True

    # Arbitrary uncurated models remain rejected
    assert _is_model_allowed(provider, "claude-unknown-99") is False
