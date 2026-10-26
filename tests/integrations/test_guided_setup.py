"""Guided setup hand-offs, discovery, and verify-before-save regressions."""

from __future__ import annotations

import json
from collections.abc import Sequence
from http import HTTPStatus
from pathlib import Path
from typing import Any

import httpx
import pytest
import requests

from integrations import setup_flow
from integrations.posthog import setup_guide as posthog
from integrations.posthog.setup import POSTHOG_SETUP
from integrations.setup import runner as guided_setup
from integrations.setup_flow import SetupUI
from integrations.telegram import setup_discovery as telegram
from integrations.telegram.setup import TELEGRAM_SETUP


class ScriptedUI(SetupUI):
    def __init__(self, answers: list[str], choices: list[str]) -> None:
        self.answers = iter(answers)
        self.choices = iter(choices)
        self.messages: list[str] = []
        self.prompts: list[tuple[str, bool]] = []

    def say(self, message: str) -> None:
        self.messages.append(message)

    def value(self, message: str, *, default: str = "", secret: bool = False) -> str:
        # Instructions must precede every human credential hand-off.
        assert any("https://" in text for text in self.messages)
        assert isinstance(default, str)
        self.prompts.append((message, secret))
        return next(self.answers)

    def choose(self, message: str, choices: Sequence[tuple[str, str]]) -> str:
        answer = next(self.choices)
        assert answer in dict(choices), (message, answer, choices)
        if answer == "cancel":
            raise KeyboardInterrupt
        return answer


@pytest.fixture
def saved(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[tuple[str, dict[str, Any]]]:
    writes: list[tuple[str, dict[str, Any]]] = []
    monkeypatch.setattr(guided_setup, "get_integration", lambda _service: None)
    monkeypatch.setattr(setup_flow, "sync_env_secret", lambda *_args: None)
    monkeypatch.setattr(setup_flow, "sync_env_values", lambda _values: tmp_path / ".env")
    monkeypatch.setattr(setup_flow, "push_webapp_org_integration", lambda *_args: None)
    monkeypatch.setattr(
        setup_flow, "upsert_integration", lambda service, value: writes.append((service, value))
    )
    return writes


def _response(payload: object, status: HTTPStatus = HTTPStatus.OK) -> requests.Response:
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps(payload).encode()
    return response


@pytest.mark.parametrize("surface", ["cli", "onboard"])
def test_telegram_handoffs_retry_and_save_selected_chat(
    monkeypatch: pytest.MonkeyPatch,
    saved: list[tuple[str, dict[str, Any]]],
    surface: str,
) -> None:
    ui = ScriptedUI(["bad-secret", "good-secret"], ["edit", "discover", "discover", "-22", "retry"])
    calls: list[tuple[str, dict[str, Any]]] = []
    discoveries = 0
    resolutions = 0

    def get(url: str, **kwargs: Any) -> requests.Response:
        nonlocal discoveries, resolutions
        assert not saved
        method = url.rsplit("/", 1)[1]
        calls.append((method, kwargs))
        if "bad-secret" in url:
            return _response({"ok": False}, HTTPStatus.UNAUTHORIZED)
        if method == "getMe":
            return _response({"ok": True, "result": {"username": "example_bot"}})
        if method == "getWebhookInfo":
            return _response({"ok": True, "result": {"url": ""}})
        if method == "getUpdates":
            discoveries += 1
            updates = (
                []
                if discoveries == 1
                else [
                    {"message": {"chat": {"id": 11, "first_name": "Other", "type": "private"}}},
                    {"message": {"chat": {"id": -22, "title": "Chosen", "type": "group"}}},
                ]
            )
            return _response({"ok": True, "result": updates})
        assert method == "getChat"
        assert kwargs["params"]["chat_id"] == "-22"
        resolutions += 1
        if resolutions == 1:
            return _response({"ok": False, "description": "Please retry"})
        return _response({"ok": True, "result": {"id": -22, "title": "Chosen", "type": "group"}})

    monkeypatch.setattr(requests, "get", get)
    monkeypatch.setattr(guided_setup, "TerminalSetupUI", lambda: ui)
    if surface == "cli":
        from integrations.cli import cmd_setup

        assert cmd_setup("telegram") == "telegram"
    else:
        from surfaces.cli.wizard.configurators.chat_notifications import _configure_telegram

        assert _configure_telegram()[0] == "Telegram"
    assert saved == [
        ("telegram", {"credentials": {"bot_token": "good-secret", "default_chat_id": "-22"}})
    ]
    assert len(ui.prompts) == 2  # Invalid token corrected; accepted values survive later failures.
    assert all(secret for _, secret in ui.prompts)
    output = "\n".join(ui.messages)
    assert "https://t.me/BotFather" in output
    assert "https://t.me/example_bot" in output
    assert "Verified telegram" in output and "Chosen" in output
    assert "good-secret" not in output and "bad-secret" not in output
    for method, kwargs in calls:
        assert kwargs["timeout"] == 10
        if method == "getUpdates":
            assert kwargs["params"] == {"limit": 100, "timeout": 0}


def test_telegram_preserves_webhook_and_cancels_without_saving(
    monkeypatch: pytest.MonkeyPatch, saved: list[tuple[str, dict[str, Any]]]
) -> None:
    methods: list[str] = []

    def get(url: str, **kwargs: Any) -> requests.Response:
        method = url.rsplit("/", 1)[1]
        methods.append(method)
        data = (
            {"username": "example_bot"}
            if method == "getMe"
            else {"url": "https://existing.example/hook"}
        )
        return _response({"ok": True, "result": data})

    monkeypatch.setattr(requests, "get", get)
    ui = ScriptedUI(["secret"], ["discover", "cancel"])
    with pytest.raises(KeyboardInterrupt):
        guided_setup.run_guided_setup(TELEGRAM_SETUP, ui=ui)
    assert methods == ["getMe", "getWebhookInfo"]
    assert not saved
    assert any("connection will stay active" in message for message in ui.messages)


def test_telegram_errors_do_not_echo_token(monkeypatch: pytest.MonkeyPatch) -> None:
    def get(url: str, **kwargs: Any) -> requests.Response:
        raise requests.ConnectionError(url)

    monkeypatch.setattr(requests, "get", get)
    with pytest.raises(telegram.TelegramSetupError) as error:
        telegram.discover_bot("private-token")
    assert "private-token" not in str(error.value)


def test_posthog_discovers_project_and_retries_without_reentering_key(
    monkeypatch: pytest.MonkeyPatch, saved: list[tuple[str, dict[str, Any]]]
) -> None:
    ui = ScriptedUI(["phx_secret"], ["https://eu.posthog.com", "retry", "42"])
    attempts = 0

    def get(url: str, **kwargs: Any) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        assert url == "https://eu.posthog.com/api/projects/"
        assert kwargs["headers"]["Authorization"] == "Bearer phx_secret"
        assert kwargs["timeout"] == 10 and kwargs["follow_redirects"] is False
        if attempts == 1:
            raise httpx.ConnectError("temporary")
        return httpx.Response(
            HTTPStatus.OK,
            request=httpx.Request("GET", url),
            json={
                "results": [{"id": 41, "name": "Other"}, {"id": 42, "name": "Production"}],
                "next": "https://untrusted.example/steal-key",
            },
        )

    def verify(method: str, url: str, **kwargs: Any) -> httpx.Response:
        assert not saved
        assert url == "https://eu.posthog.com/api/projects/42/"
        return httpx.Response(HTTPStatus.OK, request=httpx.Request(method, url), json={"id": 42})

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr(httpx, "request", verify)
    outcome = guided_setup.run_guided_setup(POSTHOG_SETUP, ui=ui)
    assert outcome.ok
    assert saved[0][1]["credentials"] == {
        "base_url": "https://eu.posthog.com",
        "personal_api_key": "phx_secret",
        "project_id": "42",
    }
    assert len(ui.prompts) == 1 and ui.prompts[0][1]
    assert attempts == 2  # Untrusted pagination URL was not followed.
    output = "\n".join(ui.messages)
    assert "https://eu.posthog.com/settings/user-api-keys" in output
    assert "Verified posthog" in output and "project 42" in output
    assert "phx_secret" not in output


def test_posthog_region_change_requires_a_new_key(
    monkeypatch: pytest.MonkeyPatch, saved: list[tuple[str, dict[str, Any]]]
) -> None:
    sent: list[tuple[str, str]] = []

    def projects(config: posthog.PostHogConfig) -> tuple[list[tuple[str, str]], str]:
        sent.append((config.base_url, config.personal_api_key))
        return [], "Not authorized"

    monkeypatch.setattr(posthog, "_projects", projects)
    ui = ScriptedUI(
        ["us-key", "eu-key"], ["https://us.posthog.com", "host", "https://eu.posthog.com", "cancel"]
    )
    with pytest.raises(KeyboardInterrupt):
        guided_setup.run_guided_setup(POSTHOG_SETUP, ui=ui)
    assert sent == [("https://us.posthog.com", "us-key"), ("https://eu.posthog.com", "eu-key")]
    assert not saved
