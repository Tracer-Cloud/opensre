from __future__ import annotations

from surfaces.cli.wizard import flow
from surfaces.shared.llm_setup.provider_choices import (
    FOCUSED_SETUP_PROVIDER_VALUES,
    OTHER_PROVIDER_SELECTION,
)


def test_onboarding_provider_options_put_focused_choices_first() -> None:
    values = [provider.value for provider in flow._onboarding_provider_options()]

    assert values[:3] == list(FOCUSED_SETUP_PROVIDER_VALUES)
    assert "anthropic" in values
    assert "openai" in values
    assert len(values) == len(set(values))


def test_initial_provider_choices_show_other_instead_of_full_catalog() -> None:
    values = [choice.value for choice in flow._initial_provider_choices()]

    assert values == [*FOCUSED_SETUP_PROVIDER_VALUES, OTHER_PROVIDER_SELECTION]
    assert "anthropic" not in values
    assert "codex" not in values


def test_choose_onboarding_provider_opens_other_menu(monkeypatch) -> None:
    prompts: list[str] = []
    defaults: list[str | None] = []
    responses = iter([OTHER_PROVIDER_SELECTION, "anthropic"])

    def _choose(prompt, choices, **kwargs):
        prompts.append(prompt)
        defaults.append(kwargs.get("default"))
        if prompt == "Choose another LLM provider":
            assert kwargs.get("default") in {choice.value for choice in choices}
        return next(responses)

    monkeypatch.setattr(flow, "choose", _choose)

    provider = flow._choose_onboarding_provider("openai")

    assert provider.value == "anthropic"
    assert prompts == ["Choose your LLM provider", "Choose another LLM provider"]
    assert defaults == ["openai", "anthropic"]
