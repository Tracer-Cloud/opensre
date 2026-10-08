"""Command lists retain actionable fields at narrow terminal widths."""

from __future__ import annotations

import io

import pytest
from rich.cells import cell_len
from rich.console import Console

from core.domain.work_items import WorkItem, WorkItemPriority, WorkItemStatus
from infrastructure.scheduling.task_types import TaskKind
from surfaces.interactive_shell.command_registry import tasks_cmds, work_cmds
from surfaces.interactive_shell.session import Session


@pytest.mark.parametrize("width", [40, 80, 120])
def test_work_list_keeps_title_identifier_and_offset(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    monkeypatch.setenv("COLUMNS", str(width))
    output = io.StringIO()
    console = Console(file=output, width=width, highlight=False)
    item = WorkItem(
        id="work-item-123456789abcdef",
        title="[bold]Checkout 日本 reliability[/bold]",
        priority=WorkItemPriority.URGENT,
        status=WorkItemStatus.BLOCKED,
        project="platform-services",
        due_at="2026-10-09T14:00:00+05:30",
    )
    work_cmds._render_work_table(console, [item], title="Work items")
    text = output.getvalue()
    assert f"ID: {item.id}" in text
    assert "[bold]" in text and "platform-services" in text
    assert "08:30" in text and "UTC" in text
    assert "Blocked" in text
    assert max(cell_len(line) for line in text.splitlines()) <= width


@pytest.mark.parametrize("width", [40, 80, 120])
def test_tasks_list_keeps_cancel_id_and_error(monkeypatch: pytest.MonkeyPatch, width: int) -> None:
    monkeypatch.setenv("COLUMNS", str(width))
    session = Session()
    task = session.task_registry.create(TaskKind.CLI_COMMAND, command="opensre cron list")
    task.mark_running()
    task.mark_failed("[literal] provider unavailable")
    output = io.StringIO()
    console = Console(file=output, width=width, highlight=False)
    tasks_cmds._cmd_tasks(session, console, [])
    text = output.getvalue()
    assert f"ID: {task.task_id}" in text
    assert "[literal] provider unavailable" in text
    assert "Failed" in text
    assert "/cancel <task_id>" in text
    assert max(cell_len(line) for line in text.splitlines()) <= width


def test_cron_replay_reserves_narrow_gutter_and_keeps_all_ids(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import subprocess

    from surfaces.interactive_shell.command_registry import cli_parity

    output = io.StringIO()
    console = Console(file=output, width=40, highlight=False)
    child_widths: list[int] = []

    def run_child(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        env = kwargs["env"]
        assert isinstance(env, dict)
        child_widths.append(int(env["COLUMNS"]))
        return subprocess.CompletedProcess(
            [],
            0,
            stdout="\n".join(
                ["ID: abc123def456"] + ["Schedule: 0 9 * * *"] * 15 + ["ID: final-task-id"]
            ),
            stderr="",
        )

    monkeypatch.setattr(cli_parity.subprocess, "run", run_child)
    cli_parity._cmd_cron(Session(), console, ["list"])
    assert child_widths == [35]
    text = output.getvalue()
    assert "final-task-id" in text
    assert "Ctrl+O" not in text
    assert max(cell_len(line) for line in text.splitlines()) <= 40
