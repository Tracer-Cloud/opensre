"""A stored schedule must have somewhere to deliver.

Slack may omit ``--chat-id`` when an incoming webhook or a default channel is
configured. On a bot-token-only install with neither, the task must be rejected
at schedule time rather than stored to deliver nowhere.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from surfaces.cli.commands.cron import cron_add


def _patch_scheduler_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from infrastructure.scheduling.scheduler.storage import task_store as scheduler_store

    monkeypatch.setattr(
        scheduler_store,
        "default_task_store_path",
        lambda: tmp_path / "scheduler_tasks.json",
    )


def _add(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> object:
    _patch_scheduler_store(tmp_path, monkeypatch)
    return CliRunner().invoke(
        cron_add,
        [
            "--kind",
            "manual_loop",
            "--cron",
            "0 8 * * 1-5",
            "--prompt",
            "Check open incidents.",
            "--provider",
            "slack",
        ],
    )


def test_slack_without_a_webhook_or_chat_id_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bot-token-only installs must not store a task with no destination."""
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.credentials.resolve_slack_credentials",
        lambda _params: {"access_token": "xoxb-test"},
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.credentials.resolve_slack_default_chat_id",
        lambda _params: "",
    )

    result = _add(monkeypatch, tmp_path)

    assert result.exit_code != 0, result.output
    assert "chat-id" in result.output.lower() or "webhook" in result.output.lower()


def test_slack_with_default_channel_and_no_chat_id_is_accepted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bot-token installs with SLACK_DEFAULT_CHAT_ID may omit --chat-id."""
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.credentials.resolve_slack_credentials",
        lambda _params: {"access_token": "xoxb-test"},
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.credentials.resolve_slack_default_chat_id",
        lambda _params: "C0123ABCD",
    )

    result = _add(monkeypatch, tmp_path)

    assert result.exit_code == 0, result.output
