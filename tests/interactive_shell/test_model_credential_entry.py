"""Explicit model changes use connected credentials and preserve endpoint guards."""

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
    monkeypatch.setattr(credentials, "connection_status", lambda _p: next(statuses, fallback))


@pytest.mark.parametrize("key", ["", "synthetic-key"])
def test_missing_key_directs_to_login_without_prompting_or_saving(monkeypatch, key):
    import surfaces.shared.llm_setup.auth_service as service

    _credential_status_sequence(monkeypatch, configured_after=False)
    monkeypatch.setattr(
        service,
        "configure_api_key_provider",
        lambda **_kw: pytest.fail("Model selection must not log in"),
    )
    console = _Console(key=key)
    console.input = lambda *_a, **_kw: pytest.fail("Complete model command must not prompt")
    assert switching.switch_llm_provider("openai", console) is False
    assert any("opensre auth login openai" in line for line in console.printed)


@pytest.mark.parametrize(
    "change",
    [
        lambda console: switching.switch_reasoning_model("gpt-5.5", console),
        lambda console: switching.switch_toolcall_model("gpt-5.5", console),
    ],
)
def test_model_only_changes_do_not_implicitly_leave_hosted_billing(
    monkeypatch: Any, change: Any
) -> None:
    monkeypatch.setattr(
        "config.account.account_llm_route",
        lambda: SimpleNamespace(model="gpt-5.4-mini"),
    )
    console = _Console()

    assert change(console) is False
    assert any("Select a connected provider" in line for line in console.printed)


def test_account_model_is_the_effective_model_shown_by_the_shell(monkeypatch: Any) -> None:
    from surfaces.interactive_shell.command_registry import repl_data

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
        "connection_status",
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
    monkeypatch.setattr("core.llm.factory.reset_llm_clients", lambda: None)
    monkeypatch.setattr(switching, "render_current_models", lambda *_: None)

    console = _Console()
    assert switching.switch_llm_provider("custom-openai", console) is True  # type: ignore[arg-type]
    assert synced and synced[0]["model"] == "gpt-5.4"


def test_custom_provider_no_model_uses_legacy_model(monkeypatch: Any) -> None:
    provider = PROVIDER_BY_VALUE["custom-openai"]
    monkeypatch.delenv(provider.model_env, raising=False)
    assert provider.legacy_model_env is not None
    monkeypatch.setenv(provider.legacy_model_env, "gateway-model")

    from surfaces.shared.llm_setup.selection import default_model

    assert default_model(provider) == "gateway-model"


def test_missing_subscription_directs_to_browser_login_without_prompting_for_key(monkeypatch):
    _credential_status_sequence(monkeypatch, configured_after=False)
    console = _Console()
    assert switching.switch_llm_provider("chatgpt", console) is False
    assert any("/auth login chatgpt" in line for line in console.printed)
