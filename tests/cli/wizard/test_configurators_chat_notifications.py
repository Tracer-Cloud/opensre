"""Behavior of the onboarding wizard's Telegram configurator.

Written before the migration onto the shared setup flow and kept green through
it: the prompts, validate-before-persist ordering, the retry loop on a bad
token, the credential tiers written (store + keyring + ``.env``), and the
``(label, env_path)`` return the wizard's summary screen renders.

This path is the reference for the credential-resolution contract in
``docs/adding-tools-and-integrations.md`` — the keyring/``.env`` assertions here
are what the migration had to carry over, not drop.

One assertion changed deliberately: a blank chat id used to be accepted with a
warning. It is now rejected, because every Telegram delivery path raises without
one — the warning just deferred the failure to the first real alert.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow
import surfaces.cli.wizard.configurators.chat_notifications as chat_notifications
import surfaces.cli.wizard.configurators.spec_configurator as spec_configurator

_TOKEN = "123456789:AAExampleSecretTokenValue"
_CHAT_REFERENCE = "@acme_alerts"
_CHAT_ID = "-1001234567890"
_ENV_PATH = Path("sentinel.env")


class _RecordingConsole:
    """Minimal stand-in for the wizard console that captures printed output."""

    def __init__(self) -> None:
        self.output: list[str] = []

    def print(self, *args: Any, **_kwargs: Any) -> None:
        self.output.append(" ".join(str(arg) for arg in args))

    @contextmanager
    def status(self, *_args: Any, **_kwargs: Any) -> Iterator[None]:
        yield

    @property
    def text(self) -> str:
        return "\n".join(self.output)


@dataclass(frozen=True)
class _Prompt:
    """One question the configurator asked."""

    label: str
    secret: bool
    allow_empty: bool


@dataclass
class _Wizard:
    """Scripted answers/results for one run, plus everything the run did."""

    console: _RecordingConsole = field(default_factory=_RecordingConsole)
    # Scripted inputs, consumed in order.
    answers: list[str] = field(default_factory=list)
    verify_results: list[tuple[str, str]] = field(default_factory=list)
    # Recorded effects.
    asked: list[_Prompt] = field(default_factory=list)
    verified: list[str] = field(default_factory=list)
    saved: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    keyring: list[tuple[str, str]] = field(default_factory=list)
    env_values: list[dict[str, str]] = field(default_factory=list)


@pytest.fixture
def wizard(monkeypatch: pytest.MonkeyPatch) -> _Wizard:
    """Patch every collaborator of ``_configure_telegram`` and expose the calls."""
    run = _Wizard()

    def _fake_prompt(
        label: str,
        *,
        default: str = "",
        secret: bool = False,
        allow_empty: bool = False,
        back_on_cancel: bool = False,
    ) -> str:
        run.asked.append(_Prompt(label=label, secret=secret, allow_empty=allow_empty))
        return run.answers.pop(0)

    def _fake_verify(_source: str, config: dict[str, Any]) -> dict[str, str]:
        run.verified.append(str(config.get("bot_token", "")))
        status, detail = run.verify_results.pop(0)
        return {"status": status, "detail": detail}

    def _fake_resolve(credentials: dict[str, str | None]) -> setup_flow.ResolvedCredentials:
        return setup_flow.ResolvedCredentials(
            credentials={**credentials, "default_chat_id": _CHAT_ID},
            note="Delivering to Acme Alerts (channel).",
        )

    monkeypatch.setattr(spec_configurator, "console", run.console)
    monkeypatch.setattr(spec_configurator, "integration_defaults", lambda _s: ({}, {}))
    monkeypatch.setattr(spec_configurator, "prompt_value", _fake_prompt)
    monkeypatch.setattr(spec_configurator, "render_integration_result", lambda *_a: None)
    monkeypatch.setattr(
        chat_notifications,
        "TELEGRAM_SETUP",
        replace(
            chat_notifications.TELEGRAM_SETUP,
            guide=None,
            verify=_fake_verify,
            resolve=_fake_resolve,
        ),
    )
    monkeypatch.setattr(
        setup_flow,
        "upsert_integration",
        lambda service, payload: run.saved.append((service, payload)),
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_secret", lambda key, value: run.keyring.append((key, value))
    )
    monkeypatch.setattr(
        setup_flow,
        "sync_env_values",
        lambda values, **_kw: (run.env_values.append(dict(values)), _ENV_PATH)[1],
    )
    return run


_OK = ("passed", "Connected to Telegram bot @acme_bot.")
_BAD = ("failed", "Telegram API check failed: Unauthorized")


def test_prompts_for_token_then_chat_id(wizard: _Wizard) -> None:
    wizard.answers[:] = [_TOKEN, _CHAT_REFERENCE]
    wizard.verify_results[:] = [_OK]

    chat_notifications._configure_telegram()

    asked = wizard.asked
    assert len(asked) == 2
    # Both fields are mandatory; only the token is masked.
    assert asked[0].secret is True
    assert "token" in asked[0].label.lower()
    assert asked[1].allow_empty is False
    assert "chat" in asked[1].label.lower()


def test_writes_store_keyring_and_env_on_success(wizard: _Wizard) -> None:
    """All three credential tiers are written — the contract the refactor kept."""
    wizard.answers[:] = [_TOKEN, _CHAT_REFERENCE]
    wizard.verify_results[:] = [_OK]

    label, env_path = chat_notifications._configure_telegram()

    assert wizard.saved == [
        (
            "telegram",
            {"credentials": {"bot_token": _TOKEN, "default_chat_id": _CHAT_ID}},
        )
    ]
    assert wizard.keyring == [("TELEGRAM_BOT_TOKEN", _TOKEN)]
    assert wizard.env_values == [{"TELEGRAM_DEFAULT_CHAT_ID": _CHAT_ID}]
    assert label == "Telegram"
    assert env_path == str(_ENV_PATH)


def test_validation_runs_before_anything_is_persisted(wizard: _Wizard) -> None:
    wizard.answers[:] = [_TOKEN, _CHAT_REFERENCE]
    wizard.verify_results[:] = [_OK]

    chat_notifications._configure_telegram()

    assert wizard.verified == [_TOKEN]


def test_bad_token_re_prompts_and_saves_nothing_until_it_validates(
    wizard: _Wizard,
) -> None:
    """The wizard loops on a rejected token rather than persisting junk."""
    wizard.answers[:] = ["wrong-token", _CHAT_REFERENCE, _TOKEN, _CHAT_REFERENCE]
    wizard.verify_results[:] = [_BAD, _OK]

    chat_notifications._configure_telegram()

    assert wizard.verified == ["wrong-token", _TOKEN]
    # Only the second, valid attempt reaches the store.
    assert len(wizard.saved) == 1
    assert wizard.saved[0][1]["credentials"]["bot_token"] == _TOKEN
    assert wizard.keyring == [("TELEGRAM_BOT_TOKEN", _TOKEN)]


def test_typed_channel_name_is_stored_as_the_resolved_numeric_id(wizard: _Wizard) -> None:
    wizard.answers[:] = [_TOKEN, _CHAT_REFERENCE]
    wizard.verify_results[:] = [_OK]

    chat_notifications._configure_telegram()

    assert wizard.saved[0][1]["credentials"]["default_chat_id"] == _CHAT_ID
    assert wizard.env_values == [{"TELEGRAM_DEFAULT_CHAT_ID": _CHAT_ID}]


def test_token_is_never_echoed_to_the_console(wizard: _Wizard) -> None:
    wizard.answers[:] = [_TOKEN, _CHAT_REFERENCE]
    wizard.verify_results[:] = [_OK]

    chat_notifications._configure_telegram()

    assert _TOKEN not in wizard.console.text
