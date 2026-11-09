"""Tests for credential resolution."""

from __future__ import annotations

import pytest

from infrastructure.scheduling.scheduler.credentials import (
    requires_explicit_chat_id,
    resolve_discord_credentials,
    resolve_rocketchat_credentials,
    resolve_slack_credentials,
    resolve_slack_default_chat_id,
    resolve_telegram_credentials,
)
from infrastructure.scheduling.scheduler.loop_constants import LOOP_SLACK_CHAT_ID_PARAM

_ROCKETCHAT_ENV_VARS = (
    "ROCKETCHAT_SERVER_URL",
    "ROCKETCHAT_AUTH_TOKEN",
    "ROCKETCHAT_USER_ID",
    "ROCKETCHAT_WEBHOOK_URL",
)


def _stub_slack_store(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, str]) -> None:
    def _get(service: str, key: str) -> str:
        if service != "slack":
            return ""
        return mapping.get(key, "")

    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.credentials._get_integration_credential", _get
    )
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)


class TestTelegramCredentials:
    def test_from_params(self) -> None:
        creds = resolve_telegram_credentials({"bot_token": "from_params"})
        assert creds == {"bot_token": "from_params"}

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "from_env")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_telegram_credentials({})
        assert creds == {"bot_token": "from_env"}

    def test_empty_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "",
        )
        creds = resolve_telegram_credentials({})
        assert creds == {}

    def test_from_keyring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("OPENSRE_DISABLE_KEYRING", raising=False)
        monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "from_keyring",
        )
        creds = resolve_telegram_credentials({})
        assert creds == {"bot_token": "from_keyring"}


class TestSlackCredentials:
    def test_from_params(self) -> None:
        creds = resolve_slack_credentials({"webhook_url": "https://hooks.slack.com/from-params"})
        assert creds == {"webhook_url": "https://hooks.slack.com/from-params"}

    def test_from_params_access_token_fallback(self) -> None:
        creds = resolve_slack_credentials({"access_token": "xoxb-from-params"})
        assert creds == {"access_token": "xoxb-from-params"}

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/from-env")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {"webhook_url": "https://hooks.slack.com/from-env"}

    def test_from_env_access_token_fallback(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.setenv("SLACK_ACCESS_TOKEN", "xoxp-from-access-env")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        # Isolate from a local wizard keyring that may hold SLACK_BOT_TOKEN.
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "xoxp-from-access-env" if name == "SLACK_ACCESS_TOKEN" else "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {"access_token": "xoxp-from-access-env"}

    def test_from_env_webhook_takes_priority(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/primary")
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-secondary")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "xoxb-secondary" if name == "SLACK_BOT_TOKEN" else "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {"webhook_url": "https://hooks.slack.com/primary"}

    def test_webhook_does_not_use_keyring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_ACCESS_TOKEN", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        # Even if keyring somehow held a webhook URL, scheduler must ignore it.
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: (
                "https://hooks.slack.com/from-keyring" if name == "SLACK_WEBHOOK_URL" else ""
            ),
        )
        creds = resolve_slack_credentials({})
        assert creds == {}

    def test_empty_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_ACCESS_TOKEN", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {}

    def test_bot_token_from_keyring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.delenv("SLACK_ACCESS_TOKEN", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "xoxb-from-keyring" if name == "SLACK_BOT_TOKEN" else "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {"access_token": "xoxb-from-keyring"}

    def test_env_wins_over_keyring_for_bot_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-from-env")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        # resolve_env_credential prefers env; stub must still honor that contract.
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "xoxb-from-env" if name == "SLACK_BOT_TOKEN" else "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {"access_token": "xoxb-from-env"}

    def test_store_bot_token_maps_to_access_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_slack_store(monkeypatch, {"bot_token": "xoxb-from-store"})
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "",
        )
        creds = resolve_slack_credentials({})
        assert creds == {"access_token": "xoxb-from-store"}


class TestSlackDefaultChatId:
    def test_from_params_chat_id(self) -> None:
        assert resolve_slack_default_chat_id({"chat_id": "C111"}) == "C111"

    def test_from_loop_param(self) -> None:
        assert resolve_slack_default_chat_id({LOOP_SLACK_CHAT_ID_PARAM: "C222"}) == "C222"

    def test_from_integration_store(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_slack_store(monkeypatch, {"bot_token": "xoxb-store", "default_chat_id": "C333"})
        monkeypatch.delenv("SLACK_DEFAULT_CHAT_ID", raising=False)
        monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "",
        )
        assert resolve_slack_default_chat_id({}) == "C333"

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_slack_store(monkeypatch, {})
        monkeypatch.setenv("SLACK_DEFAULT_CHAT_ID", "C444")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "xoxb-env" if name == "SLACK_BOT_TOKEN" else "",
        )
        assert resolve_slack_default_chat_id({}) == "C444"

    def test_empty_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_slack_store(monkeypatch, {})
        monkeypatch.delenv("SLACK_DEFAULT_CHAT_ID", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "",
        )
        assert resolve_slack_default_chat_id({}) == ""

    def test_store_channel_not_paired_with_env_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_slack_store(monkeypatch, {"default_chat_id": "C-store"})
        monkeypatch.delenv("SLACK_DEFAULT_CHAT_ID", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "xoxb-env" if name == "SLACK_BOT_TOKEN" else "",
        )
        assert resolve_slack_default_chat_id({}) == ""

    def test_store_channel_not_paired_with_task_token(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_slack_store(monkeypatch, {"default_chat_id": "C-store"})
        monkeypatch.setenv("SLACK_DEFAULT_CHAT_ID", "C-env")
        assert resolve_slack_default_chat_id({"access_token": "xoxb-params"}) == ""

    def test_env_channel_not_paired_with_store_token(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _stub_slack_store(monkeypatch, {"bot_token": "xoxb-store"})
        monkeypatch.setenv("SLACK_DEFAULT_CHAT_ID", "C-env")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "xoxb-env" if name == "SLACK_BOT_TOKEN" else "",
        )
        assert resolve_slack_default_chat_id({}) == ""

    def test_explicit_chat_id_wins_over_mismatched_sources(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _stub_slack_store(monkeypatch, {"default_chat_id": "C-store"})
        monkeypatch.setenv("SLACK_DEFAULT_CHAT_ID", "C-env")
        params = {"access_token": "xoxb-params", "chat_id": "C-explicit"}
        assert resolve_slack_default_chat_id(params) == "C-explicit"


class TestRequiresExplicitChatId:
    def test_slack_allows_default_channel_without_webhook(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_slack_default_chat_id",
            lambda _params: "C0123ABCD",
        )
        assert requires_explicit_chat_id("slack") is False

    def test_slack_requires_destination_without_webhook_or_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_slack_credentials",
            lambda _params: {"access_token": "xoxb-test"},
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_slack_default_chat_id",
            lambda _params: "",
        )
        assert requires_explicit_chat_id("slack") is True


class TestDiscordCredentials:
    def test_from_params(self) -> None:
        creds = resolve_discord_credentials({"bot_token": "discord_from_params"})
        assert creds == {"bot_token": "discord_from_params"}

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DISCORD_BOT_TOKEN", "discord_from_env")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        creds = resolve_discord_credentials({})
        assert creds == {"bot_token": "discord_from_env"}

    def test_empty_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "",
        )
        creds = resolve_discord_credentials({})
        assert creds == {}

    def test_from_keyring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DISCORD_BOT_TOKEN", raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "discord_from_keyring",
        )
        creds = resolve_discord_credentials({})
        assert creds == {"bot_token": "discord_from_keyring"}


class TestRocketChatCredentials:
    def test_from_params(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Keys resolve independently, so the store/env must be isolated even
        # when params are provided — otherwise a locally configured
        # webhook_url would leak into the result.
        for env_var in _ROCKETCHAT_ENV_VARS:
            monkeypatch.delenv(env_var, raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "",
        )
        creds = resolve_rocketchat_credentials(
            {
                "server_url": "https://chat.example.com",
                "auth_token": "tok_from_params",
                "user_id": "u1",
            }
        )
        assert creds == {
            "server_url": "https://chat.example.com",
            "auth_token": "tok_from_params",
            "user_id": "u1",
        }

    def test_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for env_var in _ROCKETCHAT_ENV_VARS:
            monkeypatch.delenv(env_var, raising=False)
        monkeypatch.setenv("ROCKETCHAT_SERVER_URL", "https://chat.example.com")
        monkeypatch.setenv("ROCKETCHAT_AUTH_TOKEN", "tok_from_env")
        monkeypatch.setenv("ROCKETCHAT_USER_ID", "u_env")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "tok_from_env" if name == "ROCKETCHAT_AUTH_TOKEN" else "",
        )
        creds = resolve_rocketchat_credentials({})
        assert creds == {
            "server_url": "https://chat.example.com",
            "auth_token": "tok_from_env",
            "user_id": "u_env",
        }

    def test_auth_token_from_keyring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for env_var in _ROCKETCHAT_ENV_VARS:
            monkeypatch.delenv(env_var, raising=False)
        monkeypatch.setenv("ROCKETCHAT_SERVER_URL", "https://chat.example.com")
        monkeypatch.setenv("ROCKETCHAT_USER_ID", "u_env")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "tok_from_keyring" if name == "ROCKETCHAT_AUTH_TOKEN" else "",
        )
        creds = resolve_rocketchat_credentials({})
        assert creds["auth_token"] == "tok_from_keyring"

    def test_webhook_only_from_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for env_var in _ROCKETCHAT_ENV_VARS:
            monkeypatch.delenv(env_var, raising=False)
        monkeypatch.setenv("ROCKETCHAT_WEBHOOK_URL", "https://chat.example.com/hooks/a/b")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "",
        )
        creds = resolve_rocketchat_credentials({})
        assert creds == {"webhook_url": "https://chat.example.com/hooks/a/b"}

    def test_webhook_does_not_use_keyring(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for env_var in _ROCKETCHAT_ENV_VARS:
            monkeypatch.delenv(env_var, raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: (
                "https://chat.example.com/hooks/from-keyring"
                if name == "ROCKETCHAT_WEBHOOK_URL"
                else ""
            ),
        )
        creds = resolve_rocketchat_credentials({})
        assert creds == {}

    def test_params_take_priority_over_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for env_var in _ROCKETCHAT_ENV_VARS:
            monkeypatch.delenv(env_var, raising=False)
        monkeypatch.setenv("ROCKETCHAT_AUTH_TOKEN", "tok_from_env")
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda name, **_kwargs: "tok_from_env" if name == "ROCKETCHAT_AUTH_TOKEN" else "",
        )
        creds = resolve_rocketchat_credentials({"auth_token": "tok_from_params"})
        assert creds["auth_token"] == "tok_from_params"

    def test_empty_when_nothing_configured(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for env_var in _ROCKETCHAT_ENV_VARS:
            monkeypatch.delenv(env_var, raising=False)
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials._get_integration_credential",
            lambda *_: "",
        )
        monkeypatch.setattr(
            "infrastructure.scheduling.scheduler.credentials.resolve_env_credential",
            lambda *_args, **_kwargs: "",
        )
        creds = resolve_rocketchat_credentials({})
        assert creds == {}
