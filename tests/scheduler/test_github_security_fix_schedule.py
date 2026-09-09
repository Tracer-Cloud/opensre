"""Acceptance coverage for the every-30-minutes fixing-github-security-alerts schedule."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner

import infrastructure.scheduling.scheduler.delivery_bundle as delivery_bundle
from core.agent_harness import AgentSession, is_recurring_skill, pin_recurring_skill
from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.storage.run_store import get_runs
from infrastructure.scheduling.scheduler.storage.task_store import list_tasks
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind, TaskStatus
from integrations import scheduled_skill_runner
from integrations.github import security_fix_runner
from integrations.github.security_fix_runner import (
    SECURITY_FIX_SKILL_NAME,
    render_security_fix_report,
)
from integrations.github.tools.security_fix.errors import (
    ERR_EXECUTION,
    ERR_FIX_ALREADY_OPEN,
)
from surfaces.cli.commands.cron import cron_command
from tests.scheduler._bundle import runners_with_agent

_EVERY_30_MINUTES = "*/30 * * * *"


class _RecordingDelivery:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def deliver(self, _task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        self.messages.append(message)
        return True, "", "local-1"


def _shipped(**overrides: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "success": True,
        "error_kind": None,
        "error": None,
        "owner": "acme",
        "repo": "app",
        "alert_type": "dependabot",
        "alert_number": 12,
        "alert_url": "https://github.com/acme/app/security/dependabot/12",
        "alert_summary": "Django vulnerable",
        "changed_files": ["requirements.txt"],
        "branch_name": "opensre/github-security-fix-dependabot-12-abc123",
        "pr_url": "https://github.com/acme/app/pull/9",
        "pr_number": 9,
    }
    result.update(overrides)
    return result


def test_security_fix_skill_is_schedulable_and_bypasses_the_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One cron tick fixes one finding, opens a PR, and delivers the PR URL verbatim."""
    store_path = tmp_path / "scheduler_tasks.json"
    db_path = tmp_path / "scheduler.db"
    delivery = _RecordingDelivery()
    fix_calls: list[dict[str, Any]] = []

    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: store_path,
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: db_path,
    )

    def fake_unattended_fix(**kwargs: Any) -> dict[str, Any]:
        fix_calls.append(kwargs)
        return _shipped()

    def fail_headless(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("a security-fix tick must not run a model turn")

    monkeypatch.setattr(security_fix_runner, "run_unattended_security_fix", fake_unattended_fix)
    monkeypatch.setattr(AgentSession, "run_headless_turn", fail_headless)
    delivery_bundle.ScheduledDeliveryAdapters({Provider.INTERACTIVE_SHELL: delivery}).install()

    assert is_recurring_skill(SECURITY_FIX_SKILL_NAME) is True
    created = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            SECURITY_FIX_SKILL_NAME,
            "--cron",
            _EVERY_30_MINUTES,
            "--provider",
            "interactive_shell",
            "--owner",
            "acme",
            "--repo",
            "app",
            "--workspace",
            "/srv/checkouts/app",
        ],
    )
    assert created.exit_code == 0, created.output
    task = list_tasks(store_path)[0]
    assert task.kind is TaskKind.RECURRING_SKILL
    assert task.cron == _EVERY_30_MINUTES
    assert task.skill_name == SECURITY_FIX_SKILL_NAME
    assert task.skill_revision == pin_recurring_skill(SECURITY_FIX_SKILL_NAME)[1]
    assert task.skill_inputs == {"owner": "acme", "repo": "app", "workspace": "/srv/checkouts/app"}

    success = execute_task(
        task,
        "2026-09-09T12:30Z",
        runners_with_agent(scheduled_skill_runner.run_scheduled_recurring_skill),
    )

    assert success is True
    assert fix_calls == [
        {
            "owner": "acme",
            "repo": "app",
            "alert_type": "auto",
            "workspace": "/srv/checkouts/app",
            "client": None,
        }
    ]
    assert delivery.messages == [
        "GitHub security fix — acme/app\n"
        "Opened https://github.com/acme/app/pull/9 for dependabot finding #12.\n"
        "Finding: Django vulnerable\n"
        "Changed files: requirements.txt\n"
        "Review the pull request before merging."
    ]
    runs = get_runs(task.id)
    assert len(runs) == 1
    assert runs[0].status is TaskStatus.SUCCESS


def test_security_fix_schedule_requires_repository_scope() -> None:
    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            SECURITY_FIX_SKILL_NAME,
            "--cron",
            _EVERY_30_MINUTES,
            "--provider",
            "interactive_shell",
            "--branch",
            "main",
        ],
    )

    assert result.exit_code == 2
    assert "--branch is only valid with --kind recurring_skill --skill" in result.output

    result = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            SECURITY_FIX_SKILL_NAME,
            "--cron",
            _EVERY_30_MINUTES,
            "--provider",
            "interactive_shell",
        ],
    )

    assert result.exit_code == 2
    assert "--owner and --repo are required for skill fixing-github-security-alerts" in (
        result.output
    )


def test_security_fix_report_explains_quiet_and_failed_ticks() -> None:
    quiet = render_security_fix_report(
        "acme",
        "app",
        _shipped(
            success=False,
            pr_url=None,
            error_kind=ERR_FIX_ALREADY_OPEN,
            error="Every open supported finding in acme/app already has an OpenSRE fix pull request open; no new PR was created.",
        ),
    )
    assert quiet == (
        "GitHub security fix — acme/app\n"
        "Nothing to fix this run: Every open supported finding in acme/app already has an "
        "OpenSRE fix pull request open; no new PR was created."
    )

    failed = render_security_fix_report(
        "acme",
        "app",
        _shipped(success=False, pr_url=None, error_kind=ERR_EXECUTION, error="agent\n gave up"),
    )
    assert failed == (
        "GitHub security fix — acme/app\n"
        "No pull request opened for dependabot finding #12 "
        "(https://github.com/acme/app/security/dependabot/12): agent gave up"
    )


def test_security_fix_runner_requires_owner_and_repo_inputs() -> None:
    with pytest.raises(RuntimeError, match="requires repo"):
        security_fix_runner.run_github_security_fix({"owner": "acme"})
