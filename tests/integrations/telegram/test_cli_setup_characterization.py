"""Behavior of ``opensre integrations setup telegram``.

Written before the migration onto the shared setup flow and kept green through
it: what the command prompts for, that it verifies *before* it persists, and the
credential shape it writes.

Two assertions changed deliberately with the migration, and are the point of it:

* the chat id is now **required** — a token-only Telegram integration verifies
  but cannot deliver, so accepting one just moves the failure to the first alert;
* this path now writes the keyring and ``.env`` too, not the store alone. That
  divergence from the onboarding wizard was invisible at runtime (the store is
  resolved first) and surfaced only in the deploy preflight, which reads env vars.

The network calls are stubbed by swapping the real spec's ``verify``/``resolve``
hooks, so the field definitions under test — including which are required —
remain the production ones.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

import integrations.cli as cli
import integrations.setup_flow as setup_flow
import integrations.telegram.setup as telegram_setup

_TOKEN = "123456789:AAExampleSecretTokenValue"
_CHAT_REFERENCE = "@acme_alerts"
_CHAT_ID = "-1001234567890"
_CONNECTED = "Connected to Telegram bot @acme_bot."
_RESOLVED_NOTE = "Delivering to Acme Alerts (channel)."
_ENV_PATH = Path("/tmp/opensre-test/.env")


@dataclasses.dataclass
class _Telegram:
    """Scripted API outcomes for one run, plus everything the run did."""

    verify_status: str = "passed"
    verify_detail: str = _CONNECTED
    resolve_error: str = ""

    asked: list[tuple[str, bool]] = dataclasses.field(default_factory=list)
    verified: list[dict[str, Any]] = dataclasses.field(default_factory=list)
    store: list[tuple[str, dict[str, Any]]] = dataclasses.field(default_factory=list)
    keyring: list[tuple[str, str]] = dataclasses.field(default_factory=list)
    env: list[dict[str, str]] = dataclasses.field(default_factory=list)


@pytest.fixture
def telegram(monkeypatch: pytest.MonkeyPatch) -> _Telegram:
    run = _Telegram()

    def _fake_verify(source: str, config: dict[str, Any]) -> dict[str, str]:
        run.verified.append(dict(config))
        return {"status": run.verify_status, "detail": run.verify_detail}

    def _fake_resolve(credentials: dict[str, str | None]) -> setup_flow.ResolvedCredentials:
        if run.resolve_error:
            return setup_flow.ResolvedCredentials(credentials={}, error=run.resolve_error)
        return setup_flow.ResolvedCredentials(
            credentials={**credentials, "default_chat_id": _CHAT_ID}, note=_RESOLVED_NOTE
        )

    # ``_setup_telegram`` imports the spec inside the function body, so replacing
    # the module attribute is what takes effect at call time.
    monkeypatch.setattr(
        telegram_setup,
        "TELEGRAM_SETUP",
        dataclasses.replace(
            telegram_setup.TELEGRAM_SETUP, guide=None, verify=_fake_verify, resolve=_fake_resolve
        ),
    )
    monkeypatch.setattr(
        setup_flow,
        "upsert_integration",
        lambda service, payload: run.store.append((service, payload)),
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_secret", lambda key, value: run.keyring.append((key, value))
    )
    monkeypatch.setattr(
        setup_flow,
        "sync_env_values",
        lambda values, **_kw: (run.env.append(dict(values)), _ENV_PATH)[1],
    )
    return run


def _prompts(monkeypatch: pytest.MonkeyPatch, run: _Telegram, *answers: str) -> None:
    """Feed ``_p`` the given answers in prompt order, recording what was asked."""
    queue = list(answers)

    def _fake_p(label: str, default: str = "", secret: bool = False) -> str:
        run.asked.append((label, secret))
        return queue.pop(0)

    monkeypatch.setattr(cli, "_p", _fake_p)


def test_prompts_for_token_then_chat_id_and_saves_after_verifying(
    monkeypatch: pytest.MonkeyPatch, telegram: _Telegram
) -> None:
    _prompts(monkeypatch, telegram, _TOKEN, _CHAT_REFERENCE)

    cli._setup_telegram()

    # The token is collected as a secret; the chat id is not.
    assert [secret for _label, secret in telegram.asked] == [True, False]
    assert "token" in telegram.asked[0][0].lower()
    assert "chat" in telegram.asked[1][0].lower()

    assert telegram.verified == [{"bot_token": _TOKEN, "default_chat_id": _CHAT_REFERENCE}]
    assert telegram.store == [
        ("telegram", {"credentials": {"bot_token": _TOKEN, "default_chat_id": _CHAT_ID}})
    ]


def test_credentials_reach_the_keyring_and_env_not_just_the_store(
    monkeypatch: pytest.MonkeyPatch, telegram: _Telegram
) -> None:
    """The drift this path used to have: store-only writes broke deploy preflight."""
    _prompts(monkeypatch, telegram, _TOKEN, _CHAT_REFERENCE)

    cli._setup_telegram()

    assert telegram.keyring == [("TELEGRAM_BOT_TOKEN", _TOKEN)]
    assert telegram.env == [{"TELEGRAM_DEFAULT_CHAT_ID": _CHAT_ID}]


def test_typed_channel_name_is_stored_as_the_resolved_numeric_id(
    monkeypatch: pytest.MonkeyPatch, telegram: _Telegram
) -> None:
    """Users can supply @name; delivery gets the id, which cannot be renamed."""
    _prompts(monkeypatch, telegram, _TOKEN, _CHAT_REFERENCE)

    cli._setup_telegram()

    assert telegram.store[0][1]["credentials"]["default_chat_id"] == _CHAT_ID


def test_blank_chat_id_is_asked_again_not_accepted(
    monkeypatch: pytest.MonkeyPatch, telegram: _Telegram
) -> None:
    """Token-only setup used to be allowed; it produces an undeliverable integration.

    A blank chat id is refused at the prompt and asked again — never saved,
    never a fatal exit that throws the user out of setup.
    """
    # Arrange — blank chat id first, then a real one on the re-ask
    _prompts(monkeypatch, telegram, _TOKEN, "", _CHAT_REFERENCE)

    # Act
    cli._setup_telegram()

    # Assert — the chat id was asked twice; nothing verified/saved until it was real
    labels = [label for label, _secret in telegram.asked]
    assert labels.count("Default chat ID or @channelname") == 2
    assert telegram.verified and telegram.store


def test_unreachable_chat_exits_without_saving(
    monkeypatch: pytest.MonkeyPatch, telegram: _Telegram
) -> None:
    """A valid token pointed at a chat the bot cannot see is not a setup."""
    telegram.resolve_error = "Telegram cannot reach chat @typo_channel: chat not found"
    _prompts(monkeypatch, telegram, _TOKEN, "@typo_channel")

    with pytest.raises(SystemExit):
        cli._setup_telegram()

    assert (telegram.store, telegram.keyring, telegram.env) == ([], [], [])


def test_blank_token_is_asked_again_before_the_next_prompt(
    monkeypatch: pytest.MonkeyPatch, telegram: _Telegram
) -> None:
    """Catch the blank field where it happens — re-ask it — not after the rest."""
    # Arrange — blank token, then the real token, then the chat id
    _prompts(monkeypatch, telegram, "", _TOKEN, _CHAT_REFERENCE)

    # Act
    cli._setup_telegram()

    # Assert — the token prompt ran twice before the chat id prompt was ever reached
    labels = [label for label, _secret in telegram.asked]
    assert labels[:2] == ["Telegram bot token", "Telegram bot token"]
    assert telegram.verified and telegram.store


def test_failed_verification_exits_without_saving(
    monkeypatch: pytest.MonkeyPatch, telegram: _Telegram
) -> None:
    """A bad token must not overwrite a working integration."""
    telegram.verify_status = "failed"
    telegram.verify_detail = "Telegram API check failed: Unauthorized"
    _prompts(monkeypatch, telegram, _TOKEN, _CHAT_REFERENCE)

    with pytest.raises(SystemExit):
        cli._setup_telegram()

    assert (telegram.store, telegram.keyring, telegram.env) == ([], [], [])


def test_success_reports_the_bot_the_chat_and_the_verify_follow_up(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], telegram: _Telegram
) -> None:
    """The bot label, resolved chat, and `integrations verify` step are user-visible."""
    _prompts(monkeypatch, telegram, _TOKEN, _CHAT_REFERENCE)

    cli._setup_telegram()

    out = capsys.readouterr().out
    assert "@acme_bot" in out
    assert "Acme Alerts (channel)" in out
    assert "opensre integrations verify telegram" in out
    assert _TOKEN not in out


def test_setup_handler_is_registered_for_telegram() -> None:
    """The dispatch entry is what makes `integrations setup telegram` reachable."""
    handler: Callable[[], None] = cli._HANDLERS["telegram"]
    assert handler is cli._setup_telegram
