"""Acceptance coverage for first-class recurring morning-report schedules."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import infrastructure.scheduling.scheduler.delivery_bundle as delivery_bundle
from core.agent_harness import AgentSession, ToolCallingTurnResult, TurnResult
from infrastructure.scheduling.scheduler.executor import execute_task
from infrastructure.scheduling.scheduler.storage.run_store import get_runs
from infrastructure.scheduling.scheduler.storage.task_store import list_tasks
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind, TaskStatus
from integrations import scheduled_skill_runner
from surfaces.cli.commands.cron import cron_command
from tests.scheduler._bundle import runners_with_agent


class _RecordingDelivery:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def deliver(self, _task: ScheduledTask, message: str) -> tuple[bool, str, str]:
        self.messages.append(message)
        return True, "", "local-1"


def test_scheduled_morning_report_runs_the_skill_and_delivers_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cron tick repeats the previewed skill instead of an investigation summary."""
    store_path = tmp_path / "scheduler_tasks.json"
    db_path = tmp_path / "scheduler.db"
    delivery = _RecordingDelivery()
    prompts: list[str] = []

    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.task_store.default_task_store_path",
        lambda: store_path,
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.database.default_run_database_path",
        lambda: db_path,
    )

    def fake_prefetch(name: str, inputs: dict[str, str]) -> str:
        assert name == "morning-report"
        assert inputs == {"city": "New Delhi"}
        return "Weather: New Delhi: sunny\nHeadlines:\n- Skill headline"

    def fake_headless(message: str, **kwargs: object) -> TurnResult:
        prompts.append(message)
        assert kwargs["unattended"] is True
        return TurnResult(
            final_intent="handled",
            action_result=ToolCallingTurnResult(
                planned_count=0,
                executed_count=0,
                executed_success_count=0,
                has_unhandled_clause=False,
                handled=True,
            ),
            assistant_response_text=(
                "Good morning! Here is your briefing.\n"
                "Weather — New Delhi: sunny\n"
                "Top headlines:\n- Skill headline"
            ),
        )

    monkeypatch.setattr(scheduled_skill_runner, "_prefetched_context", fake_prefetch)
    monkeypatch.setattr(AgentSession, "run_headless_turn", fake_headless)
    delivery_bundle.ScheduledDeliveryAdapters({Provider.INTERACTIVE_SHELL: delivery}).install()

    created = CliRunner().invoke(
        cron_command,
        [
            "add",
            "--kind",
            "recurring_skill",
            "--skill",
            "morning-report",
            "--cron",
            "0 8 * * 1-5",
            "--provider",
            "interactive_shell",
            "--city",
            "New Delhi",
        ],
    )
    assert created.exit_code == 0, created.output
    task = list_tasks(store_path)[0]
    assert task.kind is TaskKind.RECURRING_SKILL
    assert task.skill_name == "morning-report"
    assert task.skill_revision
    assert task.skill_inputs == {"city": "New Delhi"}

    success = execute_task(
        task,
        "2026-09-05T08:00Z",
        runners_with_agent(scheduled_skill_runner.run_scheduled_recurring_skill),
    )

    assert success is True
    assert len(prompts) == 1
    assert "MORNING REPORT SKILL" in prompts[0]
    assert "Daily Reliability Summary" not in prompts[0]
    assert delivery.messages == [
        "Good morning! Here is your briefing.\n"
        "Weather — New Delhi: sunny\n"
        "Top headlines:\n- Skill headline"
    ]
    runs = get_runs(task.id)
    assert len(runs) == 1
    assert runs[0].status is TaskStatus.SUCCESS
