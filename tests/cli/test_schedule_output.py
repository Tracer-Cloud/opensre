"""Responsive scheduler output keeps identifiers and failures usable."""

from __future__ import annotations

from dataclasses import replace

import pytest
from click.testing import CliRunner
from rich.cells import cell_len
from rich.console import Console

import surfaces.cli.commands.cron as cron_module
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.types import Provider, TaskKind


@pytest.mark.parametrize("width", [40, 60, 80, 120])
def test_cron_list_reflows_without_losing_identifiers_or_schedule_errors(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    loop = LoopSummary(
        id="ecf7c2580b83deadbeef0123456789abcd",
        task_ids=("ecf7c2580b83deadbeef0123456789abcd",),
        name="[bold]Checkout 日本 reliability checks[/bold]",
        description="[literal] " + "Checkout evidence " * 8 + "FULL DESCRIPTION END",
        prompt="",
        kind=TaskKind.MANUAL_LOOP,
        cron="*/15 * * * *",
        timezone="Asia/Kolkata",
        provider=Provider.SLACK,
        chat_id="C123",
        channels=("slack",),
        enabled=True,
        window_hours=24,
        last_run="2026-10-09T14:00:00+05:30",
        next_run="2026-10-09T14:15:00+05:30",
    )
    invalid = replace(
        loop,
        id="invalid-task",
        task_ids=("invalid-task",),
        name="Invalid schedule",
        schedule_error="Invalid timezone: Mars/Olympus",
    )
    paused = replace(
        loop, id="paused-task", task_ids=("paused-task",), name="Paused task", enabled=False
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.loops.list_loop_summaries",
        lambda: [loop, invalid, paused],
    )
    monkeypatch.setattr(
        "surfaces.cli.commands.schedule_listing.latest_loop_runs", lambda _loops: {}
    )
    monkeypatch.setattr(cron_module, "_console", Console(width=width, highlight=False))

    result = CliRunner().invoke(cron_module.cron_command, ["list"])

    assert result.exit_code == 0, result.output
    assert "Scheduled tasks" in result.output
    assert loop.id in result.output
    assert "[bold]" in result.output
    assert "Mars/Olympus" not in result.output
    assert "Requires action" not in result.output
    assert "Invalid" in result.output
    assert cron_module._cron_task_json(invalid)["schedule_error"] == invalid.schedule_error
    assert cron_module._cron_task_json(loop)["description"] == loop.description
    assert "FULL DESCRIPTION END" not in result.output
    lines = result.output.splitlines()
    preview_index = next(i for i, line in enumerate(lines) if "[literal]" in line)
    assert not lines[preview_index - 1].strip()
    assert "Asia/Kolkata" in result.output
    assert "08:45" in result.output
    assert "Paused" in result.output
    assert "…" in result.output
    assert max(cell_len(line) for line in result.output.splitlines()) <= width
    if width < 72:
        assert "Next run:" in result.output
    else:
        assert "Schedule" in result.output and "Next run" in result.output


@pytest.mark.parametrize("width", [40, 80, 120])
def test_cron_logs_keeps_attempt_outcomes_and_full_error(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    from infrastructure.scheduling.scheduler.outcomes import WorkOutcome, WorkStatus
    from infrastructure.scheduling.scheduler.types import DeliveryOutcome, TaskRun, TaskStatus

    run = TaskRun(
        task_id="task-123",
        fire_time="2026-10-09T14:00:00Z",
        started_at="2026-10-09T14:00:00Z",
        run_id=42,
        attempt=2,
        status=TaskStatus.FAILED,
        work_outcome=WorkOutcome(status=WorkStatus.BLOCKED),
        targets=(DeliveryOutcome(provider=Provider.SLACK, ok=False),),
        posted_message_id="slack:1234567890.123456",
        error="Delivery rejected: " + "a useful explanation " * 4 + "[literal]",
        report="Retained report",
    )
    monkeypatch.setattr(
        "infrastructure.scheduling.scheduler.storage.get_runs", lambda *_a, **_k: [run]
    )
    monkeypatch.setattr(cron_module, "_console", Console(width=width, highlight=False))
    result = CliRunner().invoke(cron_module.cron_command, ["logs", "task-123"])
    assert result.exit_code == 0, result.output
    assert "Execution history" in result.output
    assert "reclaimed/failed" in result.output
    assert "blocked" in result.output and "failed" in result.output
    assert "Attempt: 2" in result.output and "Targets: 0/1" in result.output
    assert run.posted_message_id in result.output
    assert "[literal]" in result.output
    assert "Retained report" in result.output
    assert max(cell_len(line) for line in result.output.splitlines()) <= width


def test_run_history_bounds_errors_but_full_result_retains_them() -> None:
    import io

    from infrastructure.scheduling.scheduler.types import TaskRun, TaskStatus
    from surfaces.cli.commands.cron_results import print_run_history, print_run_result

    error = "[literal] " + "Upstream diagnostic " * 15 + "FULL ERROR END"
    run = TaskRun(
        task_id="task-123",
        fire_time="2026-10-09T09:00:00Z",
        run_id=7,
        status=TaskStatus.FAILED,
        error=error,
    )
    output = io.StringIO()
    console = Console(file=output, width=160)
    print_run_history(console, [run])
    assert "FULL ERROR END" not in output.getvalue()
    assert "…" in output.getvalue()
    assert "--run <Run>" in output.getvalue()
    output.seek(0)
    output.truncate()
    print_run_result(console, run)
    assert error in " ".join(output.getvalue().split())
