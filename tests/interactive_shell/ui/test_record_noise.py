"""List summaries stay compact without hiding actionable results or work reasons."""

import io

import pytest
from rich.console import Console

from core.domain.work_items import WorkItem, WorkItemPriority, WorkItemScore, WorkItemStatus
from infrastructure.scheduling.task_types import TaskKind
from surfaces.interactive_shell.command_registry import dispatch_slash
from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.tables.work_items import next_work_table, work_items_table


@pytest.mark.parametrize("width", [40, 160])
def test_tasks_omit_repeated_commands_but_keep_errors_and_progress(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("COLUMNS", str(width))
    session = Session()
    session.task_registry.create(TaskKind.CLI_COMMAND, command="queued-check")
    failed = session.task_registry.create(TaskKind.CLI_COMMAND, command="failed-check")
    failed.mark_running()
    failed.mark_failed("[literal] upstream rejected")
    running = session.task_registry.create(TaskKind.CLI_COMMAND, command="running-check")
    running.mark_running()
    running.update_progress("Checking deployment health")
    output = io.StringIO()
    dispatch_slash("/tasks", session, Console(file=output, width=width))
    text = output.getvalue()
    assert text.count("queued-check") == 1
    assert "[literal] upstream rejected" in text
    assert "Checking deployment health" in text
    lines = text.splitlines()
    for marker in ("[literal]", "Checking deployment"):
        index = next(i for i, line in enumerate(lines) if marker in line)
        assert not lines[index - 1].strip()


def test_work_hides_empty_project_but_keeps_full_ranking_reasons() -> None:
    item = WorkItem(
        id="work-123",
        title="Verify rollout",
        status=WorkItemStatus.OPEN,
        priority=WorkItemPriority.NORMAL,
    )
    reason = "Evidence " * 30 + "REASON END"
    output = io.StringIO()
    console = Console(file=output, width=80)
    console.print(work_items_table([item]))
    assert "Project:" not in output.getvalue()
    assert "work-123" in output.getvalue()
    output.seek(0)
    output.truncate()
    console.print(next_work_table([WorkItemScore(item=item, score=12, reasons=(reason,))]))
    assert reason.strip() in " ".join(output.getvalue().split())
    lines = output.getvalue().splitlines()
    index = next(i for i, line in enumerate(lines) if "Why:" in line)
    assert not lines[index - 1].strip()
