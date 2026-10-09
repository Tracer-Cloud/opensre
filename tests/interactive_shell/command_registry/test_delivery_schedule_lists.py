"""Delivery schedule lists stay usable across CLI, REPL, and transcript widths."""

from __future__ import annotations

import io
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from rich.cells import cell_len
from rich.console import Console, RenderableType

from config.constants.delivery_schedule_lists import DELIVERY_SCHEDULE_LISTS
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from surfaces.cli.commands import posthog_report, sentry_digest
from surfaces.cli.commands.command_specs import COMMAND_SPECS_BY_NAME, load_command
from surfaces.interactive_shell.command_registry import cli_parity, dispatch_slash
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.tables import delivery_schedules


def _tasks() -> list[ScheduledTask]:
    return [
        ScheduledTask(
            id=f"{kind_index:08d}{index:024d}",
            name=f"Audit 日本 {index}",
            kind=TaskKind(spec.kind),
            cron="0 8 * * *",
            timezone="Asia/Kolkata",
            provider=Provider.SLACK,
            chat_id="[literal]C123",
            params={spec.detail_key: "[literal]project-period"},
            enabled=index != 0,
            last_run="2026-10-09T14:00:00+05:30" if index == 0 else None,
        )
        for kind_index, spec in enumerate(DELIVERY_SCHEDULE_LISTS.values(), start=1)
        for index in range(8)
    ]


@pytest.mark.parametrize("path", DELIVERY_SCHEDULE_LISTS)
@pytest.mark.parametrize("width", [40, 100, 160])
@pytest.mark.parametrize("surface", ["cli", "repl"])
def test_delivery_lists_keep_full_records_and_kind_filters(
    monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...], width: int, surface: str
) -> None:
    tasks = _tasks()
    monkeypatch.setattr(delivery_schedules, "list_tasks", lambda: tasks)
    output = io.StringIO()
    console = Console(file=output, width=width, height=25, highlight=False)
    monkeypatch.setattr(
        cli_parity, "run_cli_command", Mock(side_effect=AssertionError("No capture"))
    )
    if surface == "cli":
        monkeypatch.setattr(sentry_digest, "_console", console)
        monkeypatch.setattr(posthog_report, "_console", console)
        command = load_command(COMMAND_SPECS_BY_NAME[path[0]])
        result = CliRunner().invoke(command, list(path[1:]))
        assert result.exit_code == 0, result.output
    else:
        assert dispatch_slash("/" + " ".join(path), Session(), console)
    text = output.getvalue()
    for task in tasks:
        assert (task.id in text) == (task.kind.value == DELIVERY_SCHEDULE_LISTS[path].kind)
    assert "[literal]C123" in text and "[literal]project-period" in text
    assert "Asia/Kolkata" in text and "08:30:00" in text
    assert "Paused" in text and "Active" in text
    assert "Ctrl+O" not in text
    assert max(cell_len(line) for line in text.splitlines()) <= width


@pytest.mark.parametrize("path", DELIVERY_SCHEDULE_LISTS)
def test_empty_list_and_headless_delegation(
    monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...]
) -> None:
    monkeypatch.setattr(delivery_schedules, "list_tasks", lambda: [])
    output = io.StringIO()
    session = Session()
    console = Console(file=output)
    assert dispatch_slash("/" + " ".join(path), session, console)
    assert DELIVERY_SCHEDULE_LISTS[path].empty_message in output.getvalue()
    session.terminal = None
    runner = Mock(return_value=True)
    monkeypatch.setattr(cli_parity, "run_cli_command", runner)
    assert dispatch_slash("/" + " ".join(path), session, console)
    assert runner.call_args.args[1] == list(path)
    # Explicit flags and other operations retain Click validation/execution.
    assert dispatch_slash("/" + " ".join((*path, "--help")), Session(), console)
    assert runner.call_args.args[1] == [*path, "--help"]


@pytest.mark.parametrize("path", DELIVERY_SCHEDULE_LISTS)
def test_only_exact_nested_lists_reserve_stdin(
    monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...]
) -> None:
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    assert input_policy.turn_needs_exclusive_stdin("/" + " ".join(path), Session())
    run = (*path[:-1], "run", "task-id")
    assert not input_policy.turn_needs_exclusive_stdin("/" + " ".join(run), Session())
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: False)
    assert not input_policy.turn_needs_exclusive_stdin("/" + " ".join(path), Session())


class _TranscriptOutput(io.StringIO):
    def __init__(self) -> None:
        super().__init__()
        self.renderables: list[RenderableType] = []

    def isatty(self) -> bool:
        return True

    def write_renderable(self, renderable: RenderableType) -> bool:
        self.renderables.append(renderable)
        return True


def test_delivery_list_transcript_replays_after_resize(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = _tasks()
    monkeypatch.setattr(delivery_schedules, "list_tasks", lambda: tasks)
    output = _TranscriptOutput()
    monkeypatch.setenv("COLUMNS", "160")
    monkeypatch.setattr("sys.stdout", output)
    path = next(iter(DELIVERY_SCHEDULE_LISTS))
    delivery_schedules.print_delivery_schedule_list(Console(file=output, width=160), path)
    assert len(output.renderables) == 1
    for width in (40, 100, 160):
        replay = io.StringIO()
        Console(file=replay, width=width, height=25).print(output.renderables[0])
        text = replay.getvalue()
        assert tasks[0].id in text and tasks[7].id in text
        assert "Asia/Kolkata" in text
        assert max(cell_len(line) for line in text.splitlines()) <= width
