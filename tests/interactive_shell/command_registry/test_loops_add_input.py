"""Interactive input for incomplete ``/loops add`` commands."""

from __future__ import annotations

import asyncio
import io
from pathlib import Path
from typing import Any

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.data_structures import Size
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.constants.slash_commands import QUEUED_COMMAND_KEY
from core.agent_harness.tools.tool_context import ActionToolScope
from infrastructure.scheduling.scheduler.storage import list_tasks, task_store
from surfaces.interactive_shell.command_registry import dispatch_slash, input_collection, loops_cmds
from surfaces.interactive_shell.command_registry import help as help_cmd
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.loop_input import LoopInput, build_loop_form
from tests.interactive_shell.command_registry.test_command_inputs import run_keys
from tools.interactive_shell.actions.slash import execute_slash_tool


@pytest.fixture(autouse=True)
def interactive_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(input_collection, "repl_tty_interactive", lambda: True)


def _dispatch(command: str, *, tty: bool = True) -> str:
    session = Session()
    session.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(
        command, session
    )
    output = io.StringIO()
    dispatch_slash(command, session, Console(file=output), is_tty=tty)
    return output.getvalue()


@pytest.mark.parametrize("via_help", [False, True])
def test_incomplete_add_preserves_flags_and_creates_only_after_submit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    via_help: bool,
) -> None:
    store = tmp_path / "tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store)
    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", lambda *_args: None)
    run_now: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        loops_cmds,
        "_run_loop_task_ids_once",
        lambda _console, ids: run_now.append(ids) or True,
    )

    def collect(app: Any) -> Any:
        assert not list_tasks(store)
        keys = (
            "Morning ops\r\x13Review open incidents\r\x1308:30\r\x13"
            if via_help
            else "Review open incidents\r\x1308:30\r\x13"
        )
        return run_keys(app, keys)

    monkeypatch.setattr(loops_cmds, "run_command_input", collect, raising=False)
    command = (
        '/loops add --name "Morning ops" --weekdays --tz Asia/Kolkata '
        "--mode agent --channel interactive_shell --window 12 --run-now"
    )
    if via_help:
        monkeypatch.setattr(help_cmd, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(help_cmd, "browse_help_commands", lambda _sections: "/loops")
        monkeypatch.setattr(help_cmd, "repl_choose_subcommand", lambda **_kwargs: "add")
        command = "/help"
    with create_app_session(output=DummyOutput()):
        _dispatch(command)

    tasks = list_tasks(store)
    assert len(tasks) == 1
    task = tasks[0]
    expected = (
        ("Morning ops", "30 8 * * mon-fri", "Asia/Kolkata", 12)
        if not via_help
        else ("Morning ops", "30 8 * * *", "UTC", 24)
    )
    assert (task.name, task.cron, task.timezone, task.window_hours) == expected
    assert task.params["loop_prompt"] == "Review open incidents"
    if not via_help:
        assert task.params["loop_mode"] == "agent"
    assert task.params["loop_channels"] == "interactive_shell"
    assert run_now == ([] if via_help else [(task.id,)])


def test_cancel_and_noninteractive_dispatch_do_not_create(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store)

    def cancel(app: Any) -> Any:
        return run_keys(app, "Unsaved prompt\x03")

    monkeypatch.setattr(loops_cmds, "run_command_input", cancel)
    with create_app_session(output=DummyOutput()):
        _dispatch('/loops add --name "Unsaved loop"')
    assert not list_tasks(store)

    def unexpected(_app: Any) -> None:
        raise AssertionError("Noninteractive dispatch must not open a form")

    monkeypatch.setattr(loops_cmds, "run_command_input", unexpected)
    assert "could not add loop" in _dispatch("/loops add", tty=False)
    output = _dispatch('/loops add --prompt "Review incidents" --time 08:30', tty=False)
    assert "name is required" in output
    assert not list_tasks(store)


def test_complete_add_bypasses_form_and_does_not_run_now(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store)
    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", lambda *_args: None)

    def unexpected(_app: Any) -> None:
        raise AssertionError("A complete command must execute directly")

    monkeypatch.setattr(loops_cmds, "run_command_input", unexpected)
    run_now: list[tuple[str, ...]] = []
    monkeypatch.setattr(
        loops_cmds,
        "_run_loop_task_ids_once",
        lambda _console, ids: run_now.append(ids) or True,
    )
    _dispatch(
        '/loops add --name "Morning ops" --time 08:30 '
        '--prompt "Review open incidents" --channel interactive_shell'
    )
    assert len(list_tasks(store)) == 1
    assert not run_now


def test_empty_channel_defaults_consistently_without_opening_form(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store)
    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", lambda *_args: None)

    def unexpected(_app: Any) -> None:
        raise AssertionError("A complete command must execute directly")

    monkeypatch.setattr(loops_cmds, "run_command_input", unexpected)
    _dispatch('/loops add --name "Morning ops" --prompt Review --time 08:30 --channel ""')

    task = list_tasks(store)[0]
    assert "interactive_shell" in task.params["loop_channels"].split(",")


def test_uppercase_mode_remains_invalid_when_form_collects_schedule(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store)
    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", lambda *_args: None)

    def collect(app: Any) -> Any:
        return run_keys(app, "08:30\r\x13\x03")

    monkeypatch.setattr(loops_cmds, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        _dispatch('/loops add --name "Morning ops" --prompt Review --mode AGENT')
    assert not list_tasks(store)

    output = _dispatch('/loops add --name "Morning ops" --prompt Review --time 08:30 --mode AGENT')
    assert "mode" in output.lower()
    assert not list_tasks(store)


def test_form_strips_mode_whitespace_like_direct_command(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store)
    monkeypatch.setattr(loops_cmds, "_reload_loop_scheduler", lambda *_args: None)

    _dispatch('/loops add --name Direct --prompt Review --time 08:30 --mode " agent "')

    def collect(app: Any) -> Any:
        return run_keys(app, "09:00\r\x13\x03")

    monkeypatch.setattr(loops_cmds, "run_command_input", collect)
    with create_app_session(output=DummyOutput()):
        _dispatch('/loops add --name Form --prompt Review --mode " agent "')

    tasks = list_tasks(store)
    assert [task.name for task in tasks] == ["Direct", "Form"]
    assert all(task.params["loop_mode"] == "agent" for task in tasks)


def test_form_repairs_invalid_schedule_and_lookback_without_losing_values() -> None:
    draft = LoopInput(
        name="Morning [ops] 日本",
        prompt="Review open incidents",
        time_text="bad-time",
        window_hours="bad-window",
        channels=["interactive_shell"],
    )
    with create_app_session(output=DummyOutput()):
        keys = "\x13\x01\x0b08:30\r\x13\x01\x0b12\r\x1b\x13"
        result = run_keys(build_loop_form(draft), keys)

    assert result is not None
    assert (result.name, result.prompt, result.time_text, result.window_hours) == (
        draft.name,
        draft.prompt,
        "08:30",
        "12",
    )
    assert not result.run_now


def test_form_repairs_timezone_path_without_losing_values() -> None:
    draft = LoopInput(
        name="Morning ops",
        prompt="Review open incidents",
        time_text="08:30",
        timezone="/etc/passwd",
        channels=["interactive_shell"],
    )
    with create_app_session(output=DummyOutput()):
        result = run_keys(build_loop_form(draft), "\x13\x01\x0bUTC\r\x1b\x13")

    assert result is not None
    assert (result.name, result.prompt, result.time_text, result.timezone) == (
        draft.name,
        draft.prompt,
        draft.time_text,
        "UTC",
    )


def test_empty_supplied_cron_opens_the_cron_editor() -> None:
    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=30, columns=100)

    draft, error = loops_cmds._parse_add_args(
        ["--name", "Morning ops", "--prompt", "Review incidents", "--cron", ""]
    )
    assert draft is not None and not error

    rendered: list[str] = []
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_loop_form(draft)
        app.input = pipe

        async def capture() -> None:
            await asyncio.sleep(0.05)
            screen = app.renderer.last_rendered_screen
            assert screen is not None
            text = "\n".join(
                "".join(screen.data_buffer[y][x].char for x in range(100)) for y in range(30)
            )
            rendered.append(text)
            app.exit(result=None)

        app.run(pre_run=lambda: app.create_background_task(capture()))

    assert "Cron schedule · required" in rendered[0]


@pytest.mark.parametrize("columns,rows", [(40, 12), (100, 30), (160, 40)])
def test_form_keeps_create_action_reachable(columns: int, rows: int) -> None:
    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    draft = LoopInput(
        name="[literal] 日本 loop",
        prompt="Review open incidents and summarize operational risk",
        time_text="08:30",
        channels=["interactive_shell"],
    )
    selected: list[str] = []
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_loop_form(draft)
        app.input = pipe

        def capture(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or selected:
                return
            cursor = screen.get_cursor_position(app.layout.current_window)
            line = "".join(screen.data_buffer[cursor.y][x].char for x in range(columns))
            if "Create loop" in line:
                selected.append(line)
                pipe.send_text("\r")

        async def stop_if_stalled() -> None:
            await asyncio.sleep(2)
            app.exit(result=None)

        def start() -> None:
            app.create_background_task(stop_if_stalled())
            pipe.send_text("\t" * 5)

        app.after_render += capture
        result = app.run(pre_run=start)

    assert selected and "Create loop" in selected[0]
    assert result is not None and result.name == draft.name


def test_agent_selected_incomplete_add_is_deferred(monkeypatch: pytest.MonkeyPatch) -> None:
    ports = repl_slash_ports()
    monkeypatch.setattr(ports, "tty_interactive", lambda: True)
    dispatched: list[str] = []

    def dispatch(command: str, **_kwargs: Any) -> bool:
        dispatched.append(command)
        return True

    monkeypatch.setattr(ports, "dispatch", dispatch)
    context = ActionToolScope(
        session=Session(), console=Console(file=io.StringIO()), slash_ports=ports
    )
    result = execute_slash_tool(
        {"command": "/loops", "args": ["add", "--name", "Morning ops"]}, context
    )
    assert isinstance(result, dict) and QUEUED_COMMAND_KEY in result
    assert not dispatched
