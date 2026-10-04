"""Tests for the wizard's interactive model selection prompt."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest

from surfaces.cli.wizard import components, flow
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE


def _wire_prompts(
    monkeypatch: pytest.MonkeyPatch,
    select_values: list[str],
    text_values: list[str] | None = None,
) -> None:
    select_iter = iter(select_values)
    text_iter = iter(text_values or [])

    def _mock_select(*_args: Any, **_kwargs: Any) -> Any:
        m = MagicMock()
        m.ask.return_value = next(select_iter)
        return m

    def _mock_text(*_args: Any, **_kwargs: Any) -> Any:
        m = MagicMock()
        m.ask.return_value = next(text_iter)
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)
    monkeypatch.setattr(flow.questionary, "text", _mock_text)


def test_choose_model_returns_curated_default(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = PROVIDER_BY_VALUE["anthropic"]
    _wire_prompts(monkeypatch, select_values=[provider.default_model])

    model = components.choose_model(provider, default=provider.default_model)

    assert model == provider.default_model


def test_choose_model_offers_full_curated_list(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = PROVIDER_BY_VALUE["openai"]

    captured: dict[str, list[str]] = {}

    def _mock_select(_prompt: str, choices: list[Any], **_kwargs: Any) -> Any:
        captured["values"] = [c.value for c in choices]
        m = MagicMock()
        m.ask.return_value = provider.default_model
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)

    components.choose_model(provider, default="")

    expected_curated = [opt.value for opt in provider.models]
    assert captured["values"][:-1] == expected_curated
    assert captured["values"][-1] == components.CUSTOM_MODEL_SENTINEL


def test_choose_model_preserves_saved_model_not_in_curated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = PROVIDER_BY_VALUE["openai"]

    captured: dict[str, list[str]] = {}

    def _mock_select(_prompt: str, choices: list[Any], **_kwargs: Any) -> Any:
        captured["values"] = [c.value for c in choices]
        m = MagicMock()
        m.ask.return_value = "my-tuned-gpt"
        return m

    monkeypatch.setattr(components, "select_prompt", _mock_select)

    model = components.choose_model(provider, default="my-tuned-gpt")

    assert model == "my-tuned-gpt"
    assert "my-tuned-gpt" in captured["values"]


def test_choose_model_accepts_custom_entry(monkeypatch: pytest.MonkeyPatch) -> None:
    provider = PROVIDER_BY_VALUE["anthropic"]
    _wire_prompts(
        monkeypatch,
        select_values=[components.CUSTOM_MODEL_SENTINEL],
        text_values=["claude-future-preview"],
    )

    model = components.choose_model(provider, default=provider.default_model)

    assert model == "claude-future-preview"


def test_choose_model_works_for_cli_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI providers (codex, claude-code, etc.) use the same curated picker."""
    provider = PROVIDER_BY_VALUE["codex"]
    _wire_prompts(monkeypatch, select_values=["gpt-5.4"])

    model = components.choose_model(provider, default="")

    assert model == "gpt-5.4"


class TestGpt56CatalogPresence:
    """The onboarding picker must offer Sol / Terra / Luna (#3931)."""

    @pytest.mark.parametrize("model", ["gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"])
    def test_openai_picker_lists_every_tier(self, model: str) -> None:
        values = {option.value for option in PROVIDER_BY_VALUE["openai"].models}
        assert model in values

    def test_azure_provider_has_no_static_model_picker(self) -> None:
        assert PROVIDER_BY_VALUE["azure-openai"].models == ()

    def test_openrouter_picker_uses_namespaced_ids(self) -> None:
        values = {option.value for option in PROVIDER_BY_VALUE["openrouter"].models}
        assert "openai/gpt-5.6-sol" in values

    def test_trustedrouter_picker_uses_namespaced_ids(self) -> None:
        # TrustedRouter rejects a bare model id ("gpt-5.4-mini"), so every
        # quick-pick — routing policy or pinned vendor model — must be
        # namespaced or onboarding writes a model the provider will refuse.
        values = {option.value for option in PROVIDER_BY_VALUE["trustedrouter"].models}
        assert values
        assert all("/" in value for value in values), values

    def test_codex_picker_lists_sol(self) -> None:
        values = {option.value for option in PROVIDER_BY_VALUE["codex"].models}
        assert "gpt-5.6-sol" in values

    def test_openai_default_model_is_unchanged(self) -> None:
        # #3931 explicitly does not re-point defaults; adding quick-picks
        # must not promote a GPT-5.6 tier to the default slot.
        assert PROVIDER_BY_VALUE["openai"].default_model == "gpt-5.4-mini"

    def test_openai_picker_selects_sol(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = PROVIDER_BY_VALUE["openai"]
        _wire_prompts(monkeypatch, select_values=["gpt-5.6-sol"])

        assert components.choose_model(provider, default="") == "gpt-5.6-sol"


class TestClaudeNewModelsCatalogPresence:
    """Onboarding must keep the Anthropic default and accept a curated 5.x pick."""

    def test_anthropic_default_model_is_unchanged(self) -> None:
        assert PROVIDER_BY_VALUE["anthropic"].default_model == "claude-opus-4-7"

    def test_anthropic_picker_selects_sonnet_5_5(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = PROVIDER_BY_VALUE["anthropic"]
        _wire_prompts(monkeypatch, select_values=["claude-sonnet-5-5"])

        assert components.choose_model(provider, default="") == "claude-sonnet-5-5"
