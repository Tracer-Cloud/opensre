"""The Discord setup spec — required fields and tier persistence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import integrations.setup_flow as setup_flow
from integrations.discord.setup import DISCORD_SETUP

_ENV_PATH = Path("/tmp/opensre-test/.env")


@pytest.fixture
def writes(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    captured: dict[str, Any] = {"store": [], "keyring": [], "env": []}
    monkeypatch.setattr(
        setup_flow,
        "upsert_integration",
        lambda _service, payload: captured["store"].append(payload),
    )
    monkeypatch.setattr(
        setup_flow, "sync_env_secret", lambda key, value: captured["keyring"].append((key, value))
    )
    monkeypatch.setattr(
        setup_flow,
        "sync_env_values",
        lambda values, **_kw: captured["env"].append(dict(values)) or _ENV_PATH,
    )
    monkeypatch.setattr(
        setup_flow, "_verify", lambda _spec, _creds: (True, "Discord authenticated.")
    )
    return captured


def test_completed_setup_reports_verification_detail(writes: dict[str, Any]) -> None:
    outcome = setup_flow.apply_setup(
        DISCORD_SETUP, {"bot_token": "tok", "application_id": "app-123"}
    )

    assert outcome.ok is True
    assert outcome.detail == "Discord authenticated."


def test_bot_token_is_the_only_required_field(writes: dict[str, Any]) -> None:
    outcome = setup_flow.apply_setup(DISCORD_SETUP, {"application_id": "app-123"})

    assert outcome.ok is False
    assert "bot token" in outcome.detail.lower()
    assert writes["store"] == []


def test_bot_token_goes_to_the_keyring_and_the_rest_to_env(writes: dict[str, Any]) -> None:
    setup_flow.apply_setup(
        DISCORD_SETUP,
        {
            "bot_token": "tok",
            "application_id": "app-123",
            "public_key": "pub",
            "default_channel_id": "chan-1",
        },
    )

    assert writes["keyring"] == [("DISCORD_BOT_TOKEN", "tok")]
    env = writes["env"][0]
    assert env["DISCORD_APPLICATION_ID"] == "app-123"
    # DISCORD_PUBLIC_KEY ends in _KEY but is classified non-secret, so it lands in .env.
    assert env["DISCORD_PUBLIC_KEY"] == "pub"
    assert env["DISCORD_DEFAULT_CHANNEL_ID"] == "chan-1"
