"""Tests for REPL provider/model resolution used by the welcome banner."""

from __future__ import annotations

from types import SimpleNamespace

from config.account import AccountLLMRoute
from core.llm.provider_models import resolve_provider_models
from surfaces.shared.terminal.tables import provider as ui_provider


def test_prompt_model_tracks_hosted_route_then_local_selection(monkeypatch) -> None:
    route = AccountLLMRoute(base_url="https://example.test", model="hosted-model")
    monkeypatch.setattr("config.account.account_llm_route", lambda: route)
    monkeypatch.setenv("LLM_PROVIDER", "chatgpt")
    monkeypatch.setenv("CHATGPT_MODEL", "local-model")
    assert ui_provider.detect_provider_model() == ("openai", "hosted-model")
    monkeypatch.setattr("config.account.account_llm_route", lambda: None)
    assert ui_provider.detect_provider_model() == ("chatgpt", "local-model")


def test_ui_provider_reexports_core_resolver() -> None:
    assert ui_provider.resolve_provider_models is resolve_provider_models


def test_antigravity_cli_reads_model_env(monkeypatch: object) -> None:
    monkeypatch.setenv("ANTIGRAVITY_CLI_MODEL", "gemini-2.5-pro")

    reasoning, toolcall = resolve_provider_models(SimpleNamespace(), "antigravity-cli")

    assert reasoning == "gemini-2.5-pro"
    assert toolcall == "gemini-2.5-pro"


def test_antigravity_cli_falls_back_to_cli_default(monkeypatch: object) -> None:
    monkeypatch.delenv("ANTIGRAVITY_CLI_MODEL", raising=False)

    reasoning, toolcall = resolve_provider_models(SimpleNamespace(), "antigravity-cli")

    assert reasoning == "CLI default"
    assert toolcall == "CLI default"


def test_antigravity_cli_does_not_use_hyphenated_settings_attr() -> None:
    """Regression: hyphenated provider ids are not reachable via getattr(settings, ...)."""
    settings = SimpleNamespace(**{"antigravity-cli_model": "should-not-win"})

    reasoning, toolcall = resolve_provider_models(settings, "antigravity-cli")

    assert reasoning == "CLI default"
    assert toolcall == "CLI default"


def test_openai_ignores_legacy_oauth_auth_method(monkeypatch: object) -> None:
    monkeypatch.setenv("LLM_AUTH_METHOD", "oauth")
    monkeypatch.setenv("CODEX_MODEL", "gpt-5.5")
    monkeypatch.setenv("OPENAI_REASONING_MODEL", "gpt-5.4")

    reasoning, toolcall = resolve_provider_models(SimpleNamespace(openai_model="gpt-5.4"), "openai")

    assert reasoning != "gpt-5.5"
