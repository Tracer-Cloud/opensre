"""Provider schedule lists retain complete records across CLI and REPL widths."""

from __future__ import annotations

import io
from unittest.mock import Mock

import pytest
from click.testing import CliRunner
from rich.cells import cell_len
from rich.console import Console

from config.constants.delivery_schedule_lists import DELIVERY_SCHEDULE_LISTS
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from surfaces.cli.commands import posthog_report, sentry_digest
from surfaces.interactive_shell.command_registry import cli_parity, dispatch_slash
from surfaces.interactive_shell.runtime import input_policy, slash_adapter
from surfaces.interactive_shell.session import Session
from surfaces.shared.terminal.tables import delivery_schedules


def _tasks() -> list[ScheduledTask]:
    """Build mixed provider tasks with fields that expose width regressions."""
    tasks: list[ScheduledTask] = []
    for kind_index, spec in enumerate(DELIVERY_SCHEDULE_LISTS.values(), start=1):
        for task_index in range(2):
            tasks.append(
                ScheduledTask(
                    id=f"schedule-{kind_index}-{task_index}-1234567890abcdef",
                    name=f"Audit 日本 {spec.kind} {task_index}",
                    kind=TaskKind(spec.kind),
                    cron="0 8 * * *",
                    timezone="Asia/Kolkata",
                    provider=Provider.SLACK,
                    chat_id="channel-[literal]-123",
                    params={spec.detail_key: "[literal]project-period"},
                    enabled=task_index == 0,
                    last_run="2026-10-09T14:00:00+05:30" if task_index == 0 else None,
                )
            )
    return tasks


def _cli_command(path: tuple[str, ...]):
    if path[0] == "posthog":
        return posthog_report.posthog_command
    return sentry_digest.sentry_command


def _assert_schedule_output(text: str, path: tuple[str, ...], width: int) -> None:
    """Check the user-visible fields and the terminal-width contract."""
    tasks = _tasks()
    expected_kind = DELIVERY_SCHEDULE_LISTS[path].kind
    for task in tasks:
        assert (task.id in text) is (task.kind.value == expected_kind)
    assert "[literal]project-period" in text
    assert "channel-[literal]-123" in text
    assert "Asia/Kolkata" in text
    assert "Active" in text and "Paused" in text
    assert "08:30:00 UTC" in text
    assert "Ctrl+O" not in text
    assert max(cell_len(line) for line in text.splitlines()) <= width


@pytest.mark.parametrize("path", DELIVERY_SCHEDULE_LISTS)
@pytest.mark.parametrize("width", [40, 100, 160])
def test_cli_schedule_lists_reflow_and_keep_full_ids(
    monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...], width: int
) -> None:
    tasks = _tasks()
    monkeypatch.setattr(delivery_schedules, "list_tasks", lambda: tasks)
    output = io.StringIO()
    console = Console(file=output, width=width, highlight=False)
    if path[0] == "posthog":
        monkeypatch.setattr(posthog_report, "_console", console)
    else:
        monkeypatch.setattr(sentry_digest, "_console", console)

    result = CliRunner().invoke(_cli_command(path), list(path[1:]))

    assert result.exit_code == 0, result.output
    _assert_schedule_output(output.getvalue(), path, width)


@pytest.mark.parametrize("path", DELIVERY_SCHEDULE_LISTS)
@pytest.mark.parametrize("width", [40, 100, 160])
def test_repl_schedule_lists_render_natively(
    monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...], width: int
) -> None:
    tasks = _tasks()
    monkeypatch.setattr(delivery_schedules, "list_tasks", lambda: tasks)
    runner = Mock(side_effect=AssertionError("schedule lists must not use subprocess output"))
    monkeypatch.setattr(cli_parity, "run_cli_command", runner)
    output = io.StringIO()
    console = Console(file=output, width=width, highlight=False)

    assert dispatch_slash("/" + " ".join(path), Session(), console, is_tty=True)

    _assert_schedule_output(output.getvalue(), path, width)
    runner.assert_not_called()


@pytest.mark.parametrize("path", DELIVERY_SCHEDULE_LISTS)
def test_empty_schedule_lists_keep_existing_message(
    monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...]
) -> None:
    monkeypatch.setattr(delivery_schedules, "list_tasks", lambda: [])
    output = io.StringIO()
    console = Console(file=output, width=80, highlight=False)

    assert dispatch_slash("/" + " ".join(path), Session(), console, is_tty=True)

    assert DELIVERY_SCHEDULE_LISTS[path].empty_message in output.getvalue()


@pytest.mark.parametrize("path", DELIVERY_SCHEDULE_LISTS)
def test_only_exact_schedule_lists_are_native_and_responsive(
    monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...]
) -> None:
    runner = Mock(return_value=True)
    monkeypatch.setattr(cli_parity, "run_cli_command", runner)
    monkeypatch.setattr(delivery_schedules, "list_tasks", lambda: [])
    console = Console(file=io.StringIO(), width=80)
    session = Session()

    if path[0] == "sentry":
        assert cli_parity._cmd_sentry(session, console, list(path[1:]))
    else:
        assert cli_parity._cmd_posthog(session, console, list(path[1:]))
    runner.assert_not_called()

    other_args = [*path[1:-1], "run", "task-id"]
    if path[0] == "sentry":
        assert cli_parity._cmd_sentry(session, console, other_args)
    else:
        assert cli_parity._cmd_posthog(session, console, other_args)
    assert runner.call_args.args[1] == [path[0], *other_args]

    headless = Session()
    headless.terminal = None
    if path[0] == "sentry":
        assert cli_parity._cmd_sentry(headless, console, list(path[1:]))
    else:
        assert cli_parity._cmd_posthog(headless, console, list(path[1:]))
    assert runner.call_args.args[1] == list(path)


@pytest.mark.parametrize("path", DELIVERY_SCHEDULE_LISTS)
def test_exact_schedule_lists_own_stdin_only_on_a_tty(
    monkeypatch: pytest.MonkeyPatch, path: tuple[str, ...]
) -> None:
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    session = Session()

    assert input_policy.turn_needs_exclusive_stdin("/" + " ".join(path), session)
    assert not input_policy.turn_needs_exclusive_stdin(" ".join(path), session)
    run_path = (*path[:-1], "run", "task-id")
    assert not input_policy.turn_needs_exclusive_stdin("/" + " ".join(run_path), session)

    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: False)
    assert not input_policy.turn_needs_exclusive_stdin("/" + " ".join(path), session)


def test_agent_selected_schedule_list_uses_exclusive_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Session] = []
    monkeypatch.setattr(slash_adapter, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(slash_adapter, "dispatch_slash", lambda *_args, **_kwargs: True)

    def _with_exclusive_output(session: Session, render):
        calls.append(session)
        return render()

    monkeypatch.setattr(slash_adapter, "with_exclusive_output", _with_exclusive_output)
    session = Session()

    result = slash_adapter.ReplSlashPorts().dispatch(
        "/sentry digest schedule list",
        session=session,
        console=Console(file=io.StringIO()),
        confirm_fn=None,
        is_tty=True,
    )

    assert result is True
    assert calls == [session]


def test_delivery_schedule_record_replays_after_resize(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = _tasks()
    path = next(iter(DELIVERY_SCHEDULE_LISTS))
    monkeypatch.setattr(delivery_schedules, "list_tasks", lambda: tasks)
    renderables = []
    monkeypatch.setattr(
        delivery_schedules,
        "print_repl_renderable",
        lambda _console, renderable: renderables.append(renderable),
    )

    delivery_schedules.print_delivery_schedule_list(Console(width=160), path)

    assert len(renderables) == 1
    for width in (40, 100, 160):
        replay = io.StringIO()
        Console(file=replay, width=width, highlight=False).print(renderables[0])
        _assert_schedule_output(replay.getvalue(), path, width)
