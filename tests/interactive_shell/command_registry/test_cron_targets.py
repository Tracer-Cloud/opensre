"""Cron target identity, confirmation, terminal ownership and read-only collection."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.command_inputs import needs_command_input
from config.constants.slash_commands import QUEUED_COMMAND_KEY
from config.interactive_override import forced_non_interactive
from config.principal import Actor, Principal, StorageScope
from config.scope_context import bound_storage_scope
from core.agent_harness.tools.tool_context import ActionToolScope
from infrastructure.scheduling.scheduler import delivery_plan, loops
from infrastructure.scheduling.scheduler.loop_constants import LOOP_GROUP_ID_PARAM
from infrastructure.scheduling.scheduler.loops import list_loop_summaries
from infrastructure.scheduling.scheduler.storage import task_store
from infrastructure.scheduling.scheduler.types import Provider, ScheduledTask, TaskKind
from surfaces.cli.commands.cron import cron_command
from surfaces.interactive_shell.command_registry import (
    cli_parity,
    cron_cmds,
    cron_input,
    dispatch_slash,
    input_collection,
)
from surfaces.interactive_shell.command_registry import help as help_cmds
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.cron_input.target import build_cron_target_picker
from tests.interactive_shell.command_registry.test_command_inputs import run_keys


@pytest.fixture(autouse=True)
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        task_store, "default_task_store_path", lambda: tmp_path / "scheduler" / "tasks.json"
    )
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: True)


def seed_tasks() -> tuple[ScheduledTask, ScheduledTask]:
    tasks = tuple(
        ScheduledTask(
            id=f"full-task-id-for-{provider.value}-123456789",
            name="Payments [literal] 日本",
            kind=TaskKind.MANUAL_LOOP,
            cron="30 8 * * mon-fri",
            timezone="Asia/Kolkata",
            provider=provider,
            chat_id=chat_id,
            enabled=enabled,
            params={LOOP_GROUP_ID_PARAM: "shared-loop-group", "loop_prompt": "Inspect payments"},
        )
        for provider, chat_id, enabled in (
            (Provider.SLACK, "C-payments", True),
            (Provider.TELEGRAM, "-100987654", False),
        )
    )
    path = task_store.default_task_store_path()
    path.parent.mkdir()
    path.write_text(json.dumps([task.model_dump(mode="json") for task in tasks]))
    return tasks[0], tasks[1]


def dispatch(command: str, session: Session | None = None, *, tty: bool = True) -> str:
    session = session or Session()
    if session.terminal is not None:
        session.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(
            command, session
        )
    output = io.StringIO()
    dispatch_slash(command, session, Console(file=output, width=90), is_tty=tty)
    return output.getvalue()


@pytest.mark.parametrize("via", ["typed", "cron-menu", "help"])
def test_logs_select_exact_task_from_grouped_schedule_without_confirmation(
    monkeypatch: pytest.MonkeyPatch, via: str
) -> None:
    first, second = seed_tasks()
    grouped = list_loop_summaries(read_only=True)
    assert len(grouped) == 1 and set(grouped[0].task_ids) == {first.id, second.id}
    calls: list[list[str]] = []

    def collect(app: Any) -> Any:
        # Search the channel-specific destination, then Enter opens logs immediately.
        return run_keys(app, "-100987654\r")

    def run_cli(_console: Console, args: list[str], **_kwargs: Any) -> bool:
        calls.append(args)
        return True

    monkeypatch.setattr(cron_input, "run_command_input", collect)
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    command = "/cron logs"
    if via == "cron-menu":
        monkeypatch.setattr(cron_cmds, "repl_choose_subcommand", lambda **_kwargs: "logs")
        command = "/cron"
    elif via == "help":
        monkeypatch.setattr(help_cmds, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(help_cmds, "browse_help_commands", lambda _sections: "/cron")
        monkeypatch.setattr(help_cmds, "repl_choose_subcommand", lambda **_kwargs: "logs")
        command = "/help"
    with create_app_session(output=DummyOutput()):
        dispatch(command)
    assert calls == [["cron", "logs", second.id]]


@pytest.mark.parametrize(
    "subcommand,keys",
    [
        ("logs", "\x03"),
        ("run", "\x03"),
        ("remove", "\x03"),
        ("run", "\r\x03"),
        ("remove", "\r\x03"),
        ("run", "\r\t\r"),
        ("remove", "\r\t\r"),
    ],
)
def test_cancellation_never_executes_queues_or_migrates(
    monkeypatch: pytest.MonkeyPatch, subcommand: str, keys: str, tmp_path: Path
) -> None:
    seed_tasks()
    path = task_store.default_task_store_path()
    entries = json.loads(path.read_text())
    entries[0]["kind"] = "daily_summary"
    path.write_text(json.dumps(entries))
    missing_work = tmp_path / "missing-work" / "work_items.json"
    entries.append(
        dict(
            entries[1],
            id="legacy-reminder",
            kind="work_item_reminder",
            params={"work_item_id": "missing-item", "store_path": str(missing_work)},
        )
    )
    path.write_text(json.dumps(entries))
    original = path.read_bytes()

    def cancel(app: Any) -> Any:
        return run_keys(app, keys)

    def unexpected(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("Cancellation must never execute a cron command")

    monkeypatch.setattr(cron_input, "run_command_input", cancel)
    monkeypatch.setattr(cli_parity, "run_cli_command", unexpected)
    session = Session()
    with create_app_session(output=DummyOutput()):
        dispatch(f"/cron {subcommand}", session)
    assert path.read_bytes() == original
    assert not missing_work.parent.exists()
    assert not (path.parent / "scheduler.db").exists()
    assert not session.terminal.pending_prompt_autosubmit


def test_confirmed_run_queues_exact_task_and_releases_stdin_before_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, task = seed_tasks()
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def collect(app: Any) -> Any:
        return run_keys(app, "Telegram\r\r")

    def run_cli(_console: Console, args: list[str], **kwargs: Any) -> bool:
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(cron_input, "run_command_input", collect)
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    session = Session()
    with create_app_session(output=DummyOutput()):
        dispatch("/cron run", session)
    assert calls == []
    assert session.terminal.pending_prompt_autosubmit
    queued = session.terminal.pop_pending_prompt_default()
    assert queued == f"/cron run {task.id}"
    assert not input_policy.turn_needs_exclusive_stdin(queued, session)
    dispatch(queued, session)
    assert len(calls) == 1 and calls[0][0] == ["cron", "run", task.id]
    assert calls[0][1]["subprocess_timeout"] == 5.0
    assert task.id in calls[0][1]["keep_running_hint"]


@pytest.mark.parametrize("destination", ["C-param-payments", "-100-param-payments"])
def test_parameter_backed_destinations_from_loop_creation_are_searchable_without_credentials(
    monkeypatch: pytest.MonkeyPatch, destination: str
) -> None:
    monkeypatch.setattr(
        loops, "resolve_slack_credentials", lambda _params: {"access_token": "fake"}
    )
    monkeypatch.setattr(
        loops, "resolve_telegram_credentials", lambda _params: {"bot_token": "fake"}
    )
    task = loops.create_manual_loop(
        name="Fanout payments",
        prompt="Inspect payments",
        time_text="08:30",
        channels=["interactive_shell", "slack", "telegram"],
        slack_chat_id="C-param-payments",
        telegram_chat_id="-100-param-payments",
    ).task
    assert not task.chat_id
    path = task_store.default_task_store_path()
    before = path.read_bytes()
    calls: list[list[str]] = []

    def unexpected(*_args: Any, **_kwargs: Any) -> Any:
        raise AssertionError("Picker metadata must not resolve credentials or implicit defaults")

    def collect(app: Any) -> Any:
        return run_keys(app, destination + "\r")

    def run_cli(_console: Console, args: list[str], **_kwargs: Any) -> bool:
        calls.append(args)
        return True

    monkeypatch.setattr(delivery_plan, "resolve_slack_credentials", unexpected)
    monkeypatch.setattr(delivery_plan, "resolve_telegram_default_chat_id", unexpected)
    monkeypatch.setattr(cron_input, "run_command_input", collect)
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    with create_app_session(output=DummyOutput()):
        dispatch("/cron logs")
    assert calls == [["cron", "logs", task.id]]
    assert path.read_bytes() == before


def test_confirmed_remove_deletes_only_selected_channel_task_and_logs_handles_no_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first, second = seed_tasks()
    output: list[str] = []

    def collect(app: Any) -> Any:
        return run_keys(app, "Telegram\r\r")

    def run_cli(_console: Console, args: list[str], **_kwargs: Any) -> bool:
        result = CliRunner().invoke(cron_command, args[1:])
        assert result.exit_code == 0, result.output
        output.append(result.output)
        return True

    monkeypatch.setattr(cron_input, "run_command_input", collect)
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    with create_app_session(output=DummyOutput()):
        dispatch("/cron remove")
    assert [task.id for task in task_store.list_tasks(read_only=True)] == [first.id]
    assert second.id in output[-1] and "removed" in output[-1]
    dispatch(f"/cron logs {first.id}")
    assert f"No execution history for task {first.id}" in output[-1]


@pytest.mark.parametrize("subcommand", ["logs", "run", "remove"])
def test_empty_store_is_actionable_without_opening_picker_or_creating_files(
    monkeypatch: pytest.MonkeyPatch, subcommand: str
) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("No picker or command should run for an absent store")

    monkeypatch.setattr(cron_input, "run_command_input", unexpected)
    monkeypatch.setattr(cli_parity, "run_cli_command", unexpected)
    text = dispatch(f"/cron {subcommand}")
    assert "No scheduled tasks configured" in text and "/cron add" in text
    assert not task_store.default_task_store_path().parent.exists()


@pytest.mark.parametrize("content", ["{malformed", '[{"kind": "invalid"}]', "partial"])
def test_incomplete_task_store_warns_without_creation_advice_or_partial_selection(
    monkeypatch: pytest.MonkeyPatch, content: str
) -> None:
    first, _ = seed_tasks()
    path = task_store.default_task_store_path()
    if content == "partial":
        content = json.dumps([first.model_dump(mode="json"), {"kind": "invalid"}])
    path.write_text(content)
    before = path.read_bytes()

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Incomplete stores must not open a picker or execute anything")

    monkeypatch.setattr(cron_input, "run_command_input", unexpected)
    monkeypatch.setattr(cli_parity, "run_cli_command", unexpected)
    output = dispatch("/cron remove")
    assert "Could not read all scheduled tasks" in output and "/cron status --json" in output
    assert "No scheduled tasks" not in output and "/cron add" not in output
    assert path.read_bytes() == before


def test_organization_bound_picker_receives_only_visible_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    own, legacy = seed_tasks()
    own = own.model_copy(update={"organization": "org_A"})
    legacy = legacy.model_copy(update={"id": "legacy-task", "organization": ""})
    foreign = legacy.model_copy(
        update={"id": "foreign-task", "name": "Foreign secret", "organization": "org_B"}
    )
    path = task_store.default_task_store_path()
    path.write_text(json.dumps([task.model_dump(mode="json") for task in (own, legacy, foreign)]))
    received: list[str] = []

    def build(tasks: tuple[ScheduledTask, ...], *, subcommand: str) -> object:
        assert subcommand == "logs"
        received.extend(task.id for task in tasks)
        return object()

    monkeypatch.setattr(cron_input, "build_cron_target_picker", build)
    monkeypatch.setattr(cron_input, "run_command_input", lambda _app: None)
    monkeypatch.setenv("ORGANIZATION_ID", "org_A")
    scope = StorageScope(principal=Principal.org("org_A"), actor=Actor(id="u1"))

    with bound_storage_scope(scope):
        dispatch("/cron logs")

    assert received == [own.id, legacy.id]


@pytest.mark.parametrize(
    "args",
    [
        ["logs", "exact-id", "--limit", "3", "--run", "2", "--json"],
        ["run", "exact-id", "--failed-only"],
        ["remove", "exact-id"],
        ["logs", "--json"],
        ["run", "--failed-only"],
        ["remove", "--help"],
    ],
)
def test_explicit_ids_and_flags_bypass_collection_unchanged(
    monkeypatch: pytest.MonkeyPatch, args: list[str]
) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Explicit commands and supplied flags must bypass target loading")

    calls: list[list[str]] = []

    def run_cli(_console: Console, argv: list[str], **_kwargs: Any) -> bool:
        calls.append(argv)
        return True

    monkeypatch.setattr(cron_input, "get_task_store_snapshot", unexpected)
    session = Session()
    session.terminal.exclusive_stdin_active = True
    cron_cmds.cmd_cron(session, Console(file=io.StringIO()), args, run_cli=run_cli)
    assert calls == [["cron", *args]]
    assert not needs_command_input("/cron", args)


@pytest.mark.parametrize("subcommand", ["logs", "run", "remove"])
@pytest.mark.parametrize("host", ["non-tty", "gateway", "unowned-stdin", "override"])
def test_noninteractive_commands_keep_cli_usage_without_reading_targets(
    monkeypatch: pytest.MonkeyPatch, subcommand: str, host: str
) -> None:
    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Noninteractive commands must not read targets or consume stdin")

    monkeypatch.setattr(cron_input, "get_task_store_snapshot", unexpected)
    session = Session()
    if host == "gateway":
        session.terminal = None
    elif host == "unowned-stdin":
        monkeypatch.setattr(input_policy, "turn_needs_exclusive_stdin", lambda *_args: False)
    if host == "override":
        with forced_non_interactive():
            text = dispatch(f"/cron {subcommand}", session)
    else:
        text = dispatch(f"/cron {subcommand}", session, tty=host != "non-tty")
    assert "Missing argument" in text and "TASK_ID" in text
    if session.terminal is not None:
        assert not session.terminal.pending_prompt_autosubmit


@pytest.mark.parametrize("subcommand", ["logs", "run", "remove"])
def test_agent_selected_target_action_defers_then_explicit_id_dispatches(
    monkeypatch: pytest.MonkeyPatch, subcommand: str
) -> None:
    ports = repl_slash_ports()
    monkeypatch.setattr(ports, "tty_interactive", lambda: True)
    calls: list[str] = []

    def run(command: str, **_kwargs: Any) -> bool:
        calls.append(command)
        return True

    monkeypatch.setattr(ports, "dispatch", run)
    session = Session()
    scope = ActionToolScope(session=session, console=Console(file=io.StringIO()), slash_ports=ports)
    from tools.interactive_shell.actions.slash import execute_slash_tool

    result = execute_slash_tool({"command": "/cron", "args": [subcommand]}, scope)
    assert isinstance(result, dict) and QUEUED_COMMAND_KEY in result
    assert calls == [] and session.terminal.pending_prompt_autosubmit
    queued = session.terminal.pop_pending_prompt_default()
    assert queued == f"/cron {subcommand}"
    assert input_policy.turn_needs_exclusive_stdin(queued, session)
    result = execute_slash_tool({"command": "/cron", "args": [subcommand, "exact-id"]}, scope)
    assert calls == [f"/cron {subcommand} exact-id"]
    assert not (isinstance(result, dict) and QUEUED_COMMAND_KEY in result)


@pytest.mark.parametrize("width", [40, 90, 160])
@pytest.mark.parametrize("subcommand", ["logs", "run", "remove"])
def test_terminal_render_matches_cron_list_without_duplicate_metadata(
    width: int, subcommand: str
) -> None:
    first, _ = seed_tasks()

    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=30, columns=width)

    captured: list[str] = []
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_cron_target_picker([first], subcommand=subcommand)
        app.input = pipe

        def capture(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or captured:
                return
            lines = [
                "".join(screen.data_buffer[y][x].char for x in range(width))
                for y in range(screen.height)
            ]
            if subcommand != "logs" and not any("Cancel" in line for line in lines):
                return
            captured.extend(lines)
            pipe.send_text("\x03")

        app.after_render += capture
        # Capture run/remove after selection, while waiting on the explicit action.
        assert app.run(pre_run=lambda: pipe.send_text("\r" if subcommand != "logs" else "")) is None
    text = "".join(line.strip(" │") for line in captured)
    for value in (first.id, "[literal]", "日本"):
        assert value in text
    if subcommand == "logs":
        for value in ("slack", "Active", first.cron, first.timezone, "Next run"):
            assert value in text
        if width == 160:
            for label in ("Channel:", "State:", "Schedule:", "TZ:", "Next run:"):
                assert label not in text
        assert "Enter show logs" in text
    else:
        action = "Run now" if subcommand == "run" else "Remove permanently"
        assert f"› {action}" in text and "Cancel" in text
        assert (
            "configured delivery" in text
            if subcommand == "run"
            else "cannot be undone" in text.lower()
        )
        for value in ("Active", first.cron, first.timezone, "Next run"):
            assert value not in text
    assert "Kind:" not in text
    assert "Destination:" not in text
    assert first.chat_id not in text
    assert max(len(line.rstrip()) for line in captured) <= width
