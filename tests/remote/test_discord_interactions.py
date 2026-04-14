"""Tests for Discord interaction endpoint in app/remote/server.py."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from nacl.signing import SigningKey

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_client() -> TestClient:
    """Import app lazily so env patching applied before module-level reads."""
    from app.remote.server import app

    return TestClient(app, raise_server_exceptions=False)


def _sign_body(signing_key: SigningKey, body: bytes, timestamp: str = "1234567890") -> dict[str, str]:
    """Return headers signed with the given nacl SigningKey."""
    signed = signing_key.sign(timestamp.encode() + body)
    signature = signed.signature.hex()
    return {
        "X-Signature-Ed25519": signature,
        "X-Signature-Timestamp": timestamp,
        "Content-Type": "application/json",
    }


# ---------------------------------------------------------------------------
# Signature verification
# ---------------------------------------------------------------------------


def test_discord_interactions_rejects_missing_public_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.remote.server._DISCORD_PUBLIC_KEY", "")
    client = _make_client()

    body = json.dumps({"type": 1}).encode()
    resp = client.post(
        "/discord/interactions",
        content=body,
        headers={
            "X-Signature-Ed25519": "aa" * 32,
            "X-Signature-Timestamp": "ts",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 500
    assert "DISCORD_PUBLIC_KEY" in resp.json()["detail"]


def test_discord_interactions_rejects_bad_signature(monkeypatch: pytest.MonkeyPatch) -> None:
    signing_key = SigningKey.generate()
    valid_public_key = signing_key.verify_key.encode().hex()
    monkeypatch.setattr("app.remote.server._DISCORD_PUBLIC_KEY", valid_public_key)
    client = _make_client()

    body = json.dumps({"type": 1}).encode()
    resp = client.post(
        "/discord/interactions",
        content=body,
        headers={
            "X-Signature-Ed25519": "aa" * 64,  # valid length (64 bytes) but wrong signature
            "X-Signature-Timestamp": "ts",
            "Content-Type": "application/json",
        },
    )
    assert resp.status_code == 401
    assert "Invalid request signature" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# PING (type 1)
# ---------------------------------------------------------------------------


def test_discord_interactions_ping_returns_type_1(monkeypatch: pytest.MonkeyPatch) -> None:
    signing_key = SigningKey.generate()
    monkeypatch.setattr(
        "app.remote.server._DISCORD_PUBLIC_KEY",
        signing_key.verify_key.encode().hex(),
    )
    client = _make_client()

    body = json.dumps({"type": 1}).encode()
    headers = _sign_body(signing_key, body)
    resp = client.post("/discord/interactions", content=body, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"type": 1}


def test_discord_interactions_do_not_require_api_key_when_remote_auth_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing_key = SigningKey.generate()
    monkeypatch.setattr(
        "app.remote.server._DISCORD_PUBLIC_KEY",
        signing_key.verify_key.encode().hex(),
    )
    monkeypatch.setattr("app.remote.server._AUTH_KEY", "secret-key")
    client = _make_client()

    body = json.dumps({"type": 1}).encode()
    headers = _sign_body(signing_key, body)
    resp = client.post("/discord/interactions", content=body, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"type": 1}


# ---------------------------------------------------------------------------
# APPLICATION_COMMAND (type 2)
# ---------------------------------------------------------------------------


def test_discord_interactions_command_returns_deferred_type_5(monkeypatch: pytest.MonkeyPatch) -> None:
    signing_key = SigningKey.generate()
    monkeypatch.setattr(
        "app.remote.server._DISCORD_PUBLIC_KEY",
        signing_key.verify_key.encode().hex(),
    )
    client = _make_client()

    payload = {
        "type": 2,
        "token": "interaction-token",
        "application_id": "app-id",
        "channel_id": "chan-1",
        "data": {"options": [{"name": "alert", "value": '{"alert_name": "High CPU"}'}]},
    }
    body = json.dumps(payload).encode()
    headers = _sign_body(signing_key, body)

    with patch("app.remote.server._run_discord_investigation", new_callable=AsyncMock):
        resp = client.post("/discord/interactions", content=body, headers=headers)

    assert resp.status_code == 200
    assert resp.json() == {"type": 5}


def test_discord_interactions_unsupported_type_returns_400(monkeypatch: pytest.MonkeyPatch) -> None:
    signing_key = SigningKey.generate()
    monkeypatch.setattr(
        "app.remote.server._DISCORD_PUBLIC_KEY",
        signing_key.verify_key.encode().hex(),
    )
    client = _make_client()

    body = json.dumps({"type": 99}).encode()
    headers = _sign_body(signing_key, body)
    resp = client.post("/discord/interactions", content=body, headers=headers)

    assert resp.status_code == 400
    assert "Unsupported interaction type" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# _run_discord_investigation — unit tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_discord_investigation_posts_followup_on_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.remote.server import DiscordInteraction, _run_discord_investigation

    interaction = DiscordInteraction(
        type=2,
        token="tok",
        application_id="app-id",
        channel_id="chan-1",
        data={"options": [{"name": "alert", "value": '{"alert_name": "CPU spike"}'}]},
    )

    fake_result: dict[str, Any] = {
        "root_cause": "Memory leak in service X",
        "report": "Detailed report here",
        "is_noise": False,
    }

    posted_followups: list[dict[str, Any]] = []

    def _fake_execute(**_kw: Any) -> tuple[dict[str, Any], str, str, str]:
        return fake_result, "CPU spike", "default", "high"

    def _fake_followup(app_id: str, token: str, *, embeds: list[dict[str, Any]] | None = None, **_kw: Any) -> None:
        posted_followups.append({"app_id": app_id, "token": token, "embeds": embeds})

    monkeypatch.setattr("app.remote.server._execute_investigation", _fake_execute)
    monkeypatch.setattr("app.remote.server._discord_post_followup", _fake_followup)

    await _run_discord_investigation(interaction)

    assert len(posted_followups) == 1
    assert posted_followups[0]["token"] == "tok"
    embed = posted_followups[0]["embeds"][0]
    assert "CPU spike" in embed["title"]
    assert embed["footer"]["text"] == "OpenSRE Investigation"


@pytest.mark.asyncio
async def test_run_discord_investigation_parses_plain_text_alert(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.remote.server import DiscordInteraction, _run_discord_investigation

    interaction = DiscordInteraction(
        type=2,
        token="tok",
        application_id="app-id",
        data={"options": [{"name": "alert", "value": "plain text alert description"}]},
    )

    captured_kwargs: dict[str, Any] = {}

    def _fake_execute(**kwargs: Any) -> tuple[dict[str, Any], str, str, str]:
        captured_kwargs.update(kwargs)
        return {"root_cause": "x", "report": "y", "is_noise": False}, "plain text alert description", "", ""

    monkeypatch.setattr("app.remote.server._execute_investigation", _fake_execute)
    monkeypatch.setattr("app.remote.server._discord_post_followup", lambda *_a, **_kw: None)

    await _run_discord_investigation(interaction)

    assert captured_kwargs["raw_alert"] == {
        "alert_name": "plain text alert description",
        "description": "plain text alert description",
    }


@pytest.mark.asyncio
async def test_run_discord_investigation_posts_failure_message_on_exception(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.remote.server import DiscordInteraction, _run_discord_investigation

    interaction = DiscordInteraction(
        type=2,
        token="tok",
        application_id="app-id",
        data={"options": [{"name": "alert", "value": "{}"}]},
    )

    def _raise(**_kw: Any) -> None:
        raise RuntimeError("investigation exploded")

    failure_messages: list[str] = []

    def _fake_followup(_app_id: str, _token: str, *, content: str = "", **_kw: Any) -> None:
        failure_messages.append(content)

    monkeypatch.setattr("app.remote.server._execute_investigation", _raise)
    monkeypatch.setattr("app.remote.server._discord_post_followup", _fake_followup)

    await _run_discord_investigation(interaction)

    assert len(failure_messages) == 1
    assert "failed" in failure_messages[0].lower()


@pytest.mark.asyncio
async def test_run_discord_investigation_noise_uses_grey_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.remote.server import DiscordInteraction, _run_discord_investigation

    interaction = DiscordInteraction(
        type=2,
        token="tok",
        application_id="app-id",
        data={"options": [{"name": "alert", "value": "{}"}]},
    )

    posted_embeds: list[dict[str, Any]] = []

    def _fake_execute(**_kw: Any) -> tuple[dict[str, Any], str, str, str]:
        return {"root_cause": "noise", "report": "benign", "is_noise": True}, "alert", "", ""

    def _fake_followup(_app_id: str, _token: str, *, embeds: list[dict[str, Any]] | None = None, **_kw: Any) -> None:
        if embeds:
            posted_embeds.extend(embeds)

    monkeypatch.setattr("app.remote.server._execute_investigation", _fake_execute)
    monkeypatch.setattr("app.remote.server._discord_post_followup", _fake_followup)

    await _run_discord_investigation(interaction)

    assert posted_embeds[0]["color"] == 0x95A5A6  # grey for noise


# ---------------------------------------------------------------------------
# _discord_post_followup
# ---------------------------------------------------------------------------


def test_discord_post_followup_sends_embeds(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.remote.server import _discord_post_followup

    captured: dict[str, Any] = {}

    def _fake_post(url: str, *, json: dict[str, Any], **_kw: Any) -> MagicMock:
        captured["url"] = url
        captured["json"] = json
        resp = MagicMock()
        resp.status_code = 200
        return resp

    monkeypatch.setattr("httpx.post", _fake_post)

    _discord_post_followup("app-id", "inter-token", embeds=[{"title": "Result"}])

    assert "app-id" in captured["url"]
    assert "inter-token" in captured["url"]
    assert captured["json"]["embeds"] == [{"title": "Result"}]


def test_discord_post_followup_warns_on_non_200(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.remote.server import _discord_post_followup

    def _fake_post(*_a: Any, **_kw: Any) -> MagicMock:
        resp = MagicMock()
        resp.status_code = 400
        resp.text = "Bad Request"
        return resp

    monkeypatch.setattr("httpx.post", _fake_post)
    # Should not raise
    _discord_post_followup("app-id", "token", content="hello")
