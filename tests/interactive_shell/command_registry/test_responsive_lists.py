"""Command lists retain actionable fields at narrow terminal widths."""

from __future__ import annotations

import io

import pytest
from rich.cells import cell_len
from rich.console import Console
from rich.text import Text

from core.domain.work_items import WorkItem, WorkItemPriority, WorkItemStatus
from infrastructure.scheduling.task_types import TaskKind
from surfaces.interactive_shell.command_registry import tasks_cmds, work_cmds
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.work_list import work_list_panel
from surfaces.shared.terminal.tables.work_items import work_items_table


@pytest.mark.parametrize("width", [40, 90, 160])
def test_interactive_work_list_has_bounded_content_height_frame(width: int) -> None:
    item = WorkItem(
        id="work-item-123456789abcdef",
        title="[bold]Checkout 日本 reliability[/bold]",
        project="platform-services",
        priority=WorkItemPriority.URGENT,
        status=WorkItemStatus.BLOCKED,
        due_at="2026-10-09T14:00:00+05:30",
    )
    output = io.StringIO()
    console = Console(file=output, width=width, height=80, force_terminal=True)
    work_cmds._render_work_table(console, [item], title="Work items", interactive=True)
    text = Text.from_ansi(output.getvalue()).plain
    lines = text.splitlines()
    assert lines[0].startswith("┌") and "/work list" in lines[0]
    assert lines[-1].startswith("└")
    assert all(cell_len(line) == min(width, 96) for line in lines)
    assert len(lines) < 25
    assert f"ID: {item.id}" in text
    assert "[bold]" in text and "[/bold]" in text and "日本" in text
    assert "platform-services" in text and "Blocked" in text
    assert "08:30:00" in text and "UTC" in text
    assert "Search" not in text and "Esc" not in text


def test_work_list_pipe_keeps_existing_plain_output() -> None:
    item = WorkItem(
        id="work-123",
        title="Verify rollout",
        status=WorkItemStatus.OPEN,
        priority=WorkItemPriority.NORMAL,
    )
    output = io.StringIO()
    console = Console(file=output, width=160, force_terminal=False)
    work_cmds._render_work_table(console, [item], title="Work items")
    expected = io.StringIO()
    Console(file=expected, width=160).print(work_items_table([item]))
    assert output.getvalue() == expected.getvalue()


def test_work_list_panel_reflows_on_replay_and_keeps_header_alignment() -> None:
    item = WorkItem(
        id="work-replay-123",
        title="Verify rollout",
        status=WorkItemStatus.BLOCKED,
        priority=WorkItemPriority.URGENT,
        project="payments",
    )
    panel = work_list_panel([item], title="Work items")
    for width in (160, 40, 90):
        output = io.StringIO()
        Console(file=output, width=width, color_system=None).print(panel)
        text = output.getvalue()
        assert "ID: work-replay-123" in text
        assert ("State:" in text) == (width == 40)
        assert max(cell_len(line) for line in text.splitlines()) == min(width, 96)
        if width != 40:
            header = next(line for line in text.splitlines() if "Project" in line)
            row = next(line for line in text.splitlines() if "Verify rollout" in line)
            for label, value in (
                ("Project", "payments"),
                ("State", "Blocked"),
                ("Priority", "Urgent"),
            ):
                assert header.index(label) == row.index(value)


@pytest.mark.parametrize("interactive", [False, True])
def test_work_list_empty_keeps_add_hint_in_compact_frame(interactive: bool) -> None:
    output = io.StringIO()
    console = Console(file=output, width=160, height=80, force_terminal=True)
    work_cmds._render_work_table(console, [], title="Work items", interactive=interactive)
    text = Text.from_ansi(output.getvalue()).plain
    assert "no matching work items." in text and "/work add <title>" in text
    assert ("┌" in text) is interactive
    assert len(text.splitlines()) <= 5


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


def test_cron_logs_replay_reserves_narrow_gutter_and_keeps_ids_in_pager(
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
    session = Session()
    cli_parity._cmd_cron(session, console, ["logs", "abc123"])
    assert child_widths == [35]
    text = output.getvalue()
    assert "final-task-id" not in text
    assert "Ctrl+O" in text
    assert "final-task-id" in (session.terminal.collapsed_tool_output or "")
    assert max(cell_len(line) for line in text.splitlines()) <= 40


def test_tasks_list_bounds_multiline_error_and_command() -> None:
    session = Session()
    task = session.task_registry.create(
        TaskKind.CLI_COMMAND, command="opensre " + "x" * 2000 + "command-tail"
    )
    task.mark_failed("\x1b[31m[literal] " + "e" * 2000 + "error-tail\nsecond-error-line")
    output = io.StringIO()
    tasks_cmds._cmd_tasks(session, Console(file=output, width=80), [])
    text = output.getvalue()
    assert task.task_id in text and "[literal]" in text
    assert "command-tail" not in text
    assert "error-tail" not in text and "second-error-line" not in text
    assert "…" in text and "\x1b" not in text
    assert len(text) < 800
