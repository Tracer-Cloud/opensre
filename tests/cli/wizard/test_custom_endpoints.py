"""Onboarding endpoint collection for the custom OpenAI-/Anthropic-compatible gateways."""

from __future__ import annotations

from typing import Any

import pytest

import surfaces.cli.wizard.components as ui
from surfaces.cli.wizard.custom_endpoints import (
    CUSTOM_ENDPOINT_SELECTION,
    ensure_endpoint_settings,
    onboarding_provider_choices,
    onboarding_provider_default,
    resolve_onboarding_provider,
)
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE


def test_configured_custom_openai_short_circuits_and_keeps_v1(monkeypatch: Any) -> None:
    # custom-openai keeps its /v1 verbatim (the OpenAI client appends /chat/completions).
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://localhost:4000/v1")
    result = ensure_endpoint_settings(PROVIDER_BY_VALUE["custom-openai"])
    assert result == {"CUSTOM_OPENAI_BASE_URL": "http://localhost:4000/v1"}


def test_configured_custom_anthropic_strips_trailing_v1(monkeypatch: Any) -> None:
    # custom-anthropic strips a trailing /v1 (the Anthropic SDK appends /v1/messages).
    monkeypatch.setenv("CUSTOM_ANTHROPIC_BASE_URL", "https://proxy.example.com/v1")
    result = ensure_endpoint_settings(PROVIDER_BY_VALUE["custom-anthropic"])
    assert result == {"CUSTOM_ANTHROPIC_BASE_URL": "https://proxy.example.com"}


def test_unset_endpoint_prompts(monkeypatch: Any) -> None:
    # When the endpoint env is unset the module prompts; stub the prompt to a value.
    monkeypatch.delenv("CUSTOM_OPENAI_BASE_URL", raising=False)
    monkeypatch.setattr(ui, "prompt_value", lambda *_a, **_k: "http://gw.internal:8000/v1")
    result = ensure_endpoint_settings(PROVIDER_BY_VALUE["custom-openai"])
    assert result == {"CUSTOM_OPENAI_BASE_URL": "http://gw.internal:8000/v1"}


@pytest.mark.parametrize("slug", ["custom-openai", "custom-anthropic"])
def test_blank_prompt_is_rejected(monkeypatch: Any, slug: str) -> None:
    endpoint_env = PROVIDER_BY_VALUE[slug].endpoint_env
    monkeypatch.delenv(endpoint_env, raising=False)
    monkeypatch.setattr(ui, "prompt_value", lambda *_a, **_k: "")
    assert ensure_endpoint_settings(PROVIDER_BY_VALUE[slug]) is None


def test_onboarding_collapses_custom_providers_into_one_prominent_choice() -> None:
    choices = [
        ui.Choice(value="openai", label="OpenAI"),
        ui.Choice(value="custom-openai", label="Custom OpenAI"),
        ui.Choice(value="custom-anthropic", label="Custom Anthropic"),
    ]

    result = onboarding_provider_choices(choices)

    assert result[0].value == CUSTOM_ENDPOINT_SELECTION
    assert result[0].label == "Custom API endpoint"
    values = [choice.value for choice in result]
    assert values.count(CUSTOM_ENDPOINT_SELECTION) == 1
    assert "custom-openai" not in values
    assert "custom-anthropic" not in values


def test_saved_custom_provider_defaults_to_collapsed_choice() -> None:
    assert onboarding_provider_default("custom-anthropic") == CUSTOM_ENDPOINT_SELECTION
    assert onboarding_provider_default("openai") == "openai"


def test_custom_choice_opens_compatibility_dropdown(monkeypatch: Any) -> None:
    captured: dict[str, Any] = {}

    def _choose_compatibility(prompt: str, choices: list[ui.Choice], **kwargs: Any) -> str:
        captured["prompt"] = prompt
        captured["choices"] = choices
        captured["default"] = kwargs["default"]
        return "custom-anthropic"

    monkeypatch.setattr("surfaces.cli.wizard.components.choose", _choose_compatibility)

    result = resolve_onboarding_provider(
        CUSTOM_ENDPOINT_SELECTION,
        default="custom-anthropic",
    )

    assert result == "custom-anthropic"
    assert captured["prompt"] == "Choose endpoint compatibility"
    assert captured["default"] == "custom-anthropic"
    assert [choice.value for choice in captured["choices"]] == [
        "custom-openai",
        "custom-anthropic",
    ]
