"""Confirmation output and cron validation shared by the scheduling commands.

Characterization coverage for the ``schedule add`` surface of
``opensre posthog report`` and ``opensre sentry digest``. Both commands echo the
same three-line confirmation and reject the same bad cron input, so these tests
pin that observable behavior independently of which module the logic lives in.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from surfaces.cli.commands.posthog_report import posthog_command
from surfaces.cli.commands.sentry_digest import sentry_command

_POSTHOG_PREREQUISITES = "integrations.posthog.report_prerequisites"
_SENTRY_PREREQUISITES = "integrations.sentry.digest_prerequisites"

_POSTHOG_ADD = ("report", "schedule", "add")
_SENTRY_ADD = ("digest", "schedule", "add")

_DELIVERY_ARGS = ("--provider", "telegram", "--chat-id", "-100200300")


@pytest.fixture
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Point the scheduler store at a temp file so tests never touch real tasks."""
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: tmp_path / "tasks.json",
    )


def _allow_posthog(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_POSTHOG_PREREQUISITES}.require_posthog_integration", lambda: None)
    monkeypatch.setattr(
        f"{_POSTHOG_PREREQUISITES}.require_report_delivery_provider", lambda _provider: None
    )


def _allow_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(f"{_SENTRY_PREREQUISITES}.require_sentry_integration", lambda: None)
    monkeypatch.setattr(
        f"{_SENTRY_PREREQUISITES}.require_digest_delivery_provider", lambda _provider: None
    )


def test_posthog_schedule_add_echoes_standard_confirmation(
    monkeypatch: pytest.MonkeyPatch, isolated_store: None
) -> None:
    """Storing a task reports its cron, timezone, provider, and chat destination."""
    _allow_posthog(monkeypatch)

    result = CliRunner().invoke(
        posthog_command,
        [*_POSTHOG_ADD, "--cron", "0 9 * * 1-5", "--tz", "Europe/London", *_DELIVERY_ARGS],
    )

    assert result.exit_code == 0, result.output
    assert "PostHog report task" in result.output
    assert "created." in result.output
    assert "Cron: 0 9 * * 1-5  TZ: Europe/London" in result.output
    assert "Provider: telegram  Chat: -100200300" in result.output


def test_sentry_digest_schedule_add_echoes_standard_confirmation(
    monkeypatch: pytest.MonkeyPatch, isolated_store: None
) -> None:
    """The Sentry digest add path reports the same fields in the same order."""
    _allow_sentry(monkeypatch)

    result = CliRunner().invoke(
        sentry_command,
        [*_SENTRY_ADD, "--cron", "0 8 * * *", "--tz", "UTC", *_DELIVERY_ARGS],
    )

    assert result.exit_code == 0, result.output
    assert "Sentry digest task" in result.output
    assert "created." in result.output
    assert "Cron: 0 8 * * *  TZ: UTC" in result.output
    assert "Provider: telegram  Chat: -100200300" in result.output


def test_cron_expression_must_have_five_fields(
    monkeypatch: pytest.MonkeyPatch, isolated_store: None
) -> None:
    """A four-field expression is refused before any task is stored."""
    _allow_posthog(monkeypatch)

    result = CliRunner().invoke(
        posthog_command,
        [*_POSTHOG_ADD, "--cron", "0 9 * *", *_DELIVERY_ARGS],
    )

    assert result.exit_code == 1
    assert "cron expression must have exactly 5 fields" in result.output


def test_unknown_timezone_is_refused(monkeypatch: pytest.MonkeyPatch, isolated_store: None) -> None:
    """The timezone is validated by building the trigger, not by a name list."""
    _allow_sentry(monkeypatch)

    result = CliRunner().invoke(
        sentry_command,
        [*_SENTRY_ADD, "--cron", "0 8 * * *", "--tz", "Mars/Olympus_Mons", *_DELIVERY_ARGS],
    )

    assert result.exit_code == 1
    assert "invalid cron expression or timezone" in result.output
