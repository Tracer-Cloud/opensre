"""/model set offers inline API-key entry instead of dead-ending on a missing key."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

import surfaces.interactive_shell.command_registry.model.switching as switching
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


def test_blank_key_cancels_without_saving(monkeypatch: Any) -> None:
    import surfaces.shared.llm_setup.auth_service as service

    _credential_status_sequence(monkeypatch, configured_after=False)
    saves: list[Any] = []
    monkeypatch.setattr(service, "configure_api_key_provider", lambda **kw: saves.append(kw))

    console = _Console(key="")  # blank input = cancel
    assert switching.switch_llm_provider("openai", console) is False  # type: ignore[arg-type]
    assert saves == []
    assert any("cancelled" in line for line in console.printed)


def test_pasted_key_is_saved_and_switch_proceeds(monkeypatch: Any) -> None:
    import surfaces.shared.llm_setup.auth_profiles as providers
    import surfaces.shared.llm_setup.auth_service as service
    import surfaces.shared.llm_setup.env_sync as env_sync

    _credential_status_sequence(monkeypatch, configured_after=True)
    monkeypatch.setattr(providers, "resolve_auth_profile", lambda _p: object())
    saves: list[Any] = []
    monkeypatch.setattr(service, "configure_api_key_provider", lambda **kw: saves.append(kw))
    monkeypatch.setattr(env_sync, "sync_provider_env", lambda **_: "/tmp/.env")
    monkeypatch.setattr(switching, "_reset_runtime_llm_caches", lambda: None)
    monkeypatch.setattr(switching, "render_models_table", lambda *_: None)
    monkeypatch.setattr(switching.repl_data, "load_llm_settings", lambda: {})

    console = _Console(key="sk-test-123")
    assert switching.switch_llm_provider("openai", console) is True  # type: ignore[arg-type]
    assert saves and saves[0]["api_key"] == "sk-test-123"


@pytest.mark.parametrize(
    "change",
    [
        lambda console: switching.switch_llm_provider("anthropic", console),
        lambda console: switching.switch_reasoning_model("gpt-5.5", console),
        lambda console: switching.switch_toolcall_model("gpt-5.5", console),
        lambda console: switching.restore_default_model("anthropic", console),
    ],
)
def test_account_login_blocks_every_model_change(monkeypatch: Any, change: Any) -> None:
    monkeypatch.setattr(
        "config.account.account_llm_route",
        lambda: SimpleNamespace(model="gpt-5.4-mini"),
    )
    console = _Console()

    assert change(console) is False
    assert any("managed by your OpenSRE account" in line for line in console.printed)
    assert any("opensre account logout" in line for line in console.printed)


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
    import surfaces.shared.llm_setup.env_sync as env_sync

    _configured(monkeypatch)
    monkeypatch.setenv("CUSTOM_OPENAI_BASE_URL", "http://localhost:4000/v1")
    monkeypatch.setenv("CUSTOM_OPENAI_REASONING_MODEL", "gpt-5.4")
    synced: list[Any] = []
    monkeypatch.setattr(
        env_sync, "sync_provider_env", lambda **kw: synced.append(kw) or "/tmp/.env"
    )
    monkeypatch.setattr(switching, "_reset_runtime_llm_caches", lambda: None)
    monkeypatch.setattr(switching, "render_models_table", lambda *_: None)
    monkeypatch.setattr(switching.repl_data, "load_llm_settings", lambda: {})

    console = _Console()
    assert switching.switch_llm_provider("custom-openai", console) is True  # type: ignore[arg-type]
    assert synced and synced[0]["model"] == "gpt-5.4"


def test_custom_provider_no_model_uses_legacy_model(monkeypatch: Any) -> None:
    provider = PROVIDER_BY_VALUE["custom-openai"]
    monkeypatch.delenv(provider.model_env, raising=False)
    assert provider.legacy_model_env is not None
    monkeypatch.setenv(provider.legacy_model_env, "gateway-model")

    assert switching._resolve_omitted_model(provider) == "gateway-model"
