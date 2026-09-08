"""Tests for Sentry digest CLI prerequisites."""

from __future__ import annotations

from click.testing import CliRunner

from infrastructure.scheduling.scheduler.delivery import SUPPORTED_DELIVERY_PROVIDERS
from surfaces.cli.commands.sentry_digest import _PROVIDER_CHOICES, sentry_command


def test_sentry_provider_choices_match_supported_providers_constant() -> None:
    """Discord has no digest readiness/send path, so it must stay excluded."""
    assert set(_PROVIDER_CHOICES) == {p.value for p in SUPPORTED_DELIVERY_PROVIDERS}
    assert "discord" not in _PROVIDER_CHOICES


def test_schedule_add_requires_delivery_provider(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.configured_integration_services",
        lambda: ("sentry",),
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.delivery.delivery_provider_ready",
        lambda _provider: False,
    )

    result = runner.invoke(
        sentry_command,
        [
            "digest",
            "schedule",
            "add",
            "--cron",
            "0 8 * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
        ],
    )

    assert result.exit_code == 1
    assert "Telegram is not configured" in result.output


def test_uptime_watch_add_sends_activation_notice(monkeypatch, tmp_path) -> None:
    import infrastructure.scheduling.scheduler.delivery_bundle as delivery_bundle

    # Start with no bundle so this pins that the command installs it before it
    # delivers, rather than resolving one another test happened to leave behind.
    monkeypatch.setattr(delivery_bundle, "_installed", None)
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.configured_integration_services",
        lambda: ("sentry", "telegram"),
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.delivery.delivery_provider_ready",
        lambda _provider: True,
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: tmp_path / "tasks.json",
    )
    # Exercise the real delivery path (bundle -> Telegram adapter), stubbing only
    # the vendor transport. The command must install the adapter bundle first —
    # without it this resolves no adapter and the notice fails "Unsupported provider".
    monkeypatch.setattr(
        "integrations.telegram.scheduled_delivery.resolve_telegram_credentials",
        lambda _params: {"bot_token": "x"},
    )
    delivered: list[str] = []

    def _fake_post(chat_id, text, bot_token, parse_mode="", **_kwargs):
        delivered.append(text)
        return True, "", "1"

    monkeypatch.setattr(
        "integrations.telegram.scheduled_delivery.post_telegram_message",
        _fake_post,
    )

    result = runner.invoke(
        sentry_command,
        [
            "uptime",
            "watch",
            "add",
            "--cron",
            "*/5 * * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "8117261725",
        ],
    )

    assert result.exit_code == 0
    assert "Activation notice sent" in result.output
    assert delivered
    assert "active" in delivered[0].lower()
    assert "down" in delivered[0].lower()


def test_uptime_watch_add_requires_sentry(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.configured_integration_services",
        lambda: ("telegram",),
    )

    result = runner.invoke(
        sentry_command,
        [
            "uptime",
            "watch",
            "add",
            "--cron",
            "*/5 * * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
        ],
    )

    assert result.exit_code == 1
    assert "Sentry is not configured" in result.output


def test_schedule_add_requires_sentry(monkeypatch) -> None:
    runner = CliRunner()
    monkeypatch.setattr(
        "integrations.sentry.digest_prerequisites.configured_integration_services",
        lambda: ("telegram",),
    )

    result = runner.invoke(
        sentry_command,
        [
            "digest",
            "schedule",
            "add",
            "--cron",
            "0 8 * * *",
            "--provider",
            "telegram",
            "--chat-id",
            "-100",
        ],
    )

    assert result.exit_code == 1
    assert "Sentry is not configured" in result.output
