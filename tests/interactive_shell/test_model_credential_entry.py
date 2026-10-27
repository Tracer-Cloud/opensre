"""/model set offers inline API-key entry instead of dead-ending on a missing key."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import surfaces.shared.llm_setup.model_presentation as switching
import surfaces.shared.llm_setup.selection as selection
from surfaces.shared.llm_setup.catalog import PROVIDER_BY_VALUE


class _Console:
    is_terminal = True  # switch_llm_provider only prompts on an interactive console

    def __init__(self, key: str = "") -> None:
        self._key = key
        self.printed: list[str] = []

    def print(self, message: str = "") -> None:
        self.printed.append(str(message))

    def input(self, prompt: str = "", *, password: bool = False) -> str:
        _ = (prompt, password)
        return self._key


@pytest.fixture(autouse=True)
def _no_account_model_lock(monkeypatch: Any) -> None:
    monkeypatch.setattr("config.account.account_llm_route", lambda: None)


def _credential_status_sequence(monkeypatch: Any, *, configured_after: bool) -> None:
    """First check reports missing; the re-check after a save reports the result."""
    import config.llm_auth.credentials as credentials

    statuses = iter(
        [
            SimpleNamespace(configured=False, stale=False, detail=""),
            SimpleNamespace(configured=configured_after, stale=False, detail=""),
        ]
    )
    fallback = SimpleNamespace(configured=configured_after, stale=False, detail="")
    monkeypatch.setattr(credentials, "status", lambda _p: next(statuses, fallback))


def test_explicit_set_never_prompts_for_a_missing_key(monkeypatch: Any) -> None:
    _credential_status_sequence(monkeypatch, configured_after=False)
    console = _Console()
    console.input = lambda *_args, **_kwargs: pytest.fail("explicit set must not prompt")
    assert switching.switch_llm_provider("openai", console) is False
    assert any("opensre auth login openai" in line for line in console.printed)


def test_explicit_set_does_not_save_a_supplied_console_key(monkeypatch: Any) -> None:
    _credential_status_sequence(monkeypatch, configured_after=False)
    console = _Console(key="synthetic-key")
    assert switching.switch_llm_provider("openai", console) is False
    assert all("synthetic-key" not in line for line in console.printed)


def test_account_identity_does_not_block_explicit_local_selection(monkeypatch: Any) -> None:
    monkeypatch.setattr(
        "config.account.account_llm_route", lambda: SimpleNamespace(model="hosted-model")
    )
    _configured(monkeypatch)
    monkeypatch.setattr(
        "surfaces.shared.llm_setup.env_sync.sync_provider_env", lambda **_: "/tmp/.env"
    )
    monkeypatch.setattr(selection, "_reset_runtime_llm_caches", lambda: None)
    monkeypatch.setattr(switching, "render_current_models", lambda *_: None)
    assert switching.switch_llm_provider("anthropic", _Console()) is True


def test_account_model_is_the_effective_model_shown_by_the_shell(monkeypatch: Any) -> None:
    from surfaces.shared.llm_setup import effective as repl_data

    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setattr(
        "config.account.account_llm_route",
        lambda: SimpleNamespace(
            base_url="https://app.opensre.com/api/llm/v1",
            model="gpt-account",
        ),
    )

    settings = repl_data.load_llm_settings()

    assert settings.provider == "openai"
    assert settings.openai_reasoning_model == "gpt-account"
    assert settings.openai_classification_model == "gpt-account"
    assert settings.openai_toolcall_model == "gpt-account"


def _configured(monkeypatch: Any) -> None:
    import config.llm_auth.credentials as credentials

    monkeypatch.setattr(
        credentials,
        "status",
        lambda _p: SimpleNamespace(configured=True, stale=False, detail=""),
    )


def test_custom_provider_missing_base_url_is_rejected(monkeypatch: Any) -> None:
    # /model switch to a custom gateway with no base URL must refuse, not half-write .env.
    _configured(monkeypatch)
    monkeypatch.delenv("CUSTOM_OPENAI_BASE_URL", raising=False)

    console = _Console()
    assert switching.switch_llm_provider("custom-openai", console) is False  # type: ignore[arg-type]
    assert any("missing base URL" in line for line in console.printed)


def test_custom_provider_missing_model_is_rejected(monkeypatch: Any) -> None:
    # Base URL set but no model anywhere: refuse rather than persist a blank model
    # (which would break the next config load) — the Greptile P1 fix.
    _configured(monkeypatch)
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://localhost:4000/v1")
    for var in (
        "CUSTOM_OPENAI_REASONING_MODEL",
        "CUSTOM_OPENAI_CLASSIFICATION_MODEL",
        "CUSTOM_OPENAI_TOOLCALL_MODEL",
        "CUSTOM_OPENAI_MODEL",
    ):
        monkeypatch.delenv(var, raising=False)

    console = _Console()
    assert switching.switch_llm_provider("custom-openai", console) is False  # type: ignore[arg-type]
    assert any("missing model" in line for line in console.printed)


def test_custom_provider_no_model_preserves_configured_model(monkeypatch: Any) -> None:
    # Switching a custom provider with no explicit model keeps the configured one
    # (restore-default / provider-only path) instead of blanking it.

    _configured(monkeypatch)
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://localhost:4000/v1")
    monkeypatch.setenv("CUSTOM_OPENAI_REASONING_MODEL", "gpt-5.4")
    synced: list[Any] = []
    monkeypatch.setattr(
        selection, "sync_provider_env", lambda **kw: synced.append(kw) or "/tmp/.env"
    )
    monkeypatch.setattr(selection, "_reset_runtime_llm_caches", lambda: None)
    monkeypatch.setattr(switching, "render_current_models", lambda *_: None)

    console = _Console()
    assert switching.switch_llm_provider("custom-openai", console) is True  # type: ignore[arg-type]
    assert synced and synced[0]["model"] == "gpt-5.4"


def test_custom_provider_no_model_uses_legacy_model(monkeypatch: Any) -> None:
    provider = PROVIDER_BY_VALUE["custom-openai"]
    monkeypatch.delenv(provider.model_env, raising=False)
    assert provider.legacy_model_env is not None
    monkeypatch.setenv(provider.legacy_model_env, "gateway-model")

    assert selection._resolve_omitted_model(provider) == "gateway-model"


def test_missing_subscription_directs_to_browser_login_without_prompting_for_key(monkeypatch):
    _credential_status_sequence(monkeypatch, configured_after=False)
    console = _Console()
    assert switching.switch_llm_provider("chatgpt", console) is False
    assert any("/auth login chatgpt" in line for line in console.printed)
