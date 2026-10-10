"""Cron input collection preserves CLI semantics and terminal ownership."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from rich.console import Console

from config.constants.slash_commands import QUEUED_COMMAND_KEY
from core.agent_harness.tools.tool_context import ActionToolScope
from infrastructure.scheduling.scheduler.storage import list_tasks, task_store
from surfaces.cli.commands.cron import cron_command
from surfaces.interactive_shell.command_registry import cron_cmds
from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.slash import execute_slash_tool

COMPLETE = [
    "add",
    "--kind",
    "manual_loop",
    "--provider",
    "interactive_shell",
    "--cron",
    "0 9 * * *",
    "--prompt",
    "Check [literal] 日本 incidents",
]


def test_cli_cron_preparation_preserves_values_and_rejects_without_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    store = tmp_path / "tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store)
    runner = CliRunner()
    result = runner.invoke(cron_command, [*COMPLETE, "--name", "Morning audit", "--window", "12"])
    assert result.exit_code == 0, result.output
    tasks = list_tasks(store)
    assert len(tasks) == 1
    task = tasks[0]
    assert (task.name, task.cron, task.timezone, task.window_hours) == (
        "Morning audit",
        "0 9 * * *",
        "UTC",
        12,
    )
    assert task.params["loop_prompt"] == "Check [literal] 日本 incidents"
    invalid = runner.invoke(cron_command, [*COMPLETE, "--window", "0"])
    assert invalid.exit_code != 0
    assert list_tasks(store) == tasks


def test_agent_incomplete_cron_is_deferred_without_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = repl_slash_ports()
    monkeypatch.setattr(ports, "tty_interactive", lambda: True)
    dispatched: list[str] = []

    def dispatch(command: str, **kwargs: Any) -> bool:
        dispatched.append(command)
        return True

    monkeypatch.setattr(ports, "dispatch", dispatch)
    session = Session()
    ctx = ActionToolScope(session=session, console=Console(file=io.StringIO()), slash_ports=ports)
    args = COMPLETE[:-2]
    result = execute_slash_tool({"command": "/cron", "args": args}, ctx)
    assert isinstance(result, dict) and QUEUED_COMMAND_KEY in result
    assert not dispatched


def test_missing_cron_prompt_collects_without_losing_supplied_flags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.terminal.exclusive_stdin_active = True
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: True)
    collected: list[list[str]] = []

    def collect(args: list[str]) -> list[str]:
        collected.append(args)
        return COMPLETE[1:]

    monkeypatch.setattr(cron_cmds, "collect_cron_add", collect, raising=False)
    calls: list[list[str]] = []

    def run_cli(_console: Console, args: list[str], **kwargs: Any) -> bool:
        calls.append(args)
        return True

    cron_cmds.cmd_cron(session, Console(file=io.StringIO()), COMPLETE[:-2], run_cli=run_cli)
    assert collected == [COMPLETE[1:-2]]
    assert calls == [["cron", *COMPLETE]]


def test_cron_form_real_keys_validate_and_keep_draft_after_errors() -> None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.output import DummyOutput

    from surfaces.interactive_shell.ui.cron_input import build_cron_form
    from surfaces.interactive_shell.ui.cron_input.arguments import (
        parse_cron_draft,
        visible_cron_fields,
    )
    from tests.interactive_shell.command_registry.test_command_inputs import run_keys

    values = parse_cron_draft(COMPLETE[1:])
    values["cron_expr"] = "not a schedule"
    values["name"] = "[literal] 日本"
    names = visible_cron_fields(values)
    with create_app_session(output=DummyOutput()):
        app = build_cron_form(values)
        # Invalid Create stays in the form; fix the schedule without re-entering other fields.
        keys = "\x13" + "\t" * names.index("cron_expr") + "\r\x01\x0b0 10 * * *\r\x13"
        result = run_keys(app, keys)
    assert result is not None
    draft = parse_cron_draft(result)
    assert draft["cron_expr"] == "0 10 * * *"
    assert draft["name"] == values["name"] and draft["prompt"] == values["prompt"]


def test_form_cancel_does_not_save_or_delegate(monkeypatch: pytest.MonkeyPatch) -> None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.output import DummyOutput

    from surfaces.interactive_shell.command_registry import cron_input
    from tests.interactive_shell.command_registry.test_command_inputs import run_keys

    def cancel(app: Any) -> None:
        return run_keys(app, "\t\rUnsaved name\x03")

    def unexpected(*args: Any, **kwargs: Any) -> bool:
        raise AssertionError("Cancellation must not create a task")

    monkeypatch.setattr(cron_input, "run_command_input", cancel)
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: True)
    session = Session()
    session.terminal.exclusive_stdin_active = True
    with create_app_session(output=DummyOutput()):
        assert cron_cmds.cmd_cron(session, Console(file=io.StringIO()), ["add"], run_cli=unexpected)
    assert not session.terminal.pending_prompt_autosubmit


@pytest.mark.parametrize(
    "headless,command", [(False, COMPLETE), (True, ["add"]), (False, ["add", "--help"])]
)
def test_complete_headless_and_help_bypass_the_form(
    monkeypatch: pytest.MonkeyPatch,
    headless: bool,
    command: list[str],
) -> None:
    def unexpected(_args: list[str]) -> None:
        raise AssertionError("Must not collect input")

    monkeypatch.setattr(cron_cmds, "collect_cron_add", unexpected)
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: True)
    session = Session()
    if headless:
        session.terminal = None
    else:
        session.terminal.exclusive_stdin_active = True
    calls: list[list[str]] = []

    def run_cli(_console: Console, args: list[str], **kwargs: Any) -> bool:
        calls.append(args)
        return True

    cron_cmds.cmd_cron(session, Console(file=io.StringIO()), command, run_cli=run_cli)
    assert calls == [["cron", *command]]


def test_partial_syntax_and_destination_requirements_use_actual_cli_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from infrastructure.scheduling.scheduler import credentials
    from surfaces.interactive_shell.ui.cron_input.arguments import (
        cron_add_needs_input,
        parse_cron_draft,
    )

    assert not cron_add_needs_input(COMPLETE)
    assert cron_add_needs_input([*COMPLETE[:-2], "--prompt="])
    assert not cron_add_needs_input(["add", "--unknown", "x"])
    assert not cron_add_needs_input(["add", "--name"])
    draft = parse_cron_draft(["--name=a=b", "--window", "bad", "--stateless"])
    assert (draft["name"], draft["window_hours"], draft["stateless"]) == ("a=b", "bad", "True")
    slack = [*COMPLETE, "--provider", "slack"]
    monkeypatch.setattr(credentials, "resolve_slack_credentials", lambda _params: {})
    monkeypatch.setattr(credentials, "resolve_slack_default_chat_id", lambda _params: "")
    assert cron_add_needs_input(slack)
    monkeypatch.setattr(
        credentials, "resolve_slack_credentials", lambda _params: {"webhook_url": "configured"}
    )
    assert not cron_add_needs_input(slack)


@pytest.mark.parametrize("via_help", [False, True])
def test_cron_form_persists_only_after_explicit_create(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    via_help: bool,
) -> None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.output import DummyOutput

    from surfaces.interactive_shell.command_registry import cli_parity, cron_input, dispatch_slash
    from surfaces.interactive_shell.command_registry import help as help_commands
    from surfaces.interactive_shell.ui.cron_input.arguments import (
        parse_cron_draft,
        visible_cron_fields,
    )
    from tests.interactive_shell.command_registry.test_command_inputs import run_keys

    store = tmp_path / "tasks.json"
    monkeypatch.setattr(task_store, "default_task_store_path", lambda: store)
    incomplete = COMPLETE[:-2]
    draft = parse_cron_draft(incomplete[1:])
    prompt_row = visible_cron_fields(draft).index("prompt")
    creations: list[list[str]] = []

    def collect(app: Any) -> Any:
        assert not list_tasks(store)
        return run_keys(app, "\t" * prompt_row + "\rReview deployment health\r\x13")

    def run_cli(_console: Console, args: list[str], **kwargs: Any) -> bool:
        creations.append(args)
        result = CliRunner().invoke(cron_command, args[1:])
        assert result.exit_code == 0, result.output
        return True

    monkeypatch.setattr(cron_input, "run_command_input", collect)
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: True)
    command = '/cron add --kind manual_loop --provider interactive_shell --cron "0 9 * * *"'
    if via_help:
        monkeypatch.setattr(help_commands, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(
            help_commands, "browse_help_commands", lambda _sections, selected=command: selected
        )
        command = "/help"
    session = Session()
    session.terminal.exclusive_stdin_active = True
    with create_app_session(output=DummyOutput()):
        assert dispatch_slash(command, session, Console(file=io.StringIO()), is_tty=True)
    tasks = list_tasks(store)
    assert len(tasks) == len(creations) == 1
    assert tasks[0].params["loop_prompt"] == "Review deployment health"
    assert tasks[0].last_run is None


def test_noninteractive_dispatch_override_suppresses_cron_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from config.interactive_override import forced_non_interactive

    def unexpected(_args: list[str]) -> None:
        raise AssertionError("Noninteractive dispatch cannot open a form")

    monkeypatch.setattr(cron_cmds, "collect_cron_add", unexpected)
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: True)
    session = Session()
    session.terminal.exclusive_stdin_active = True
    calls: list[list[str]] = []

    def run_cli(_console: Console, args: list[str], **kwargs: Any) -> bool:
        calls.append(args)
        return True

    with forced_non_interactive():
        cron_cmds.cmd_cron(session, Console(file=io.StringIO()), ["add"], run_cli=run_cli)
    assert calls == [["cron", "add"]]


@pytest.mark.parametrize("columns,rows", [(40, 12), (100, 30), (160, 40)])
def test_form_scroll_keeps_create_reachable_on_small_terminals(columns: int, rows: int) -> None:
    import asyncio

    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input
    from prompt_toolkit.output import DummyOutput

    from surfaces.interactive_shell.ui.cron_input import build_cron_form
    from surfaces.interactive_shell.ui.cron_input.arguments import (
        parse_cron_draft,
        visible_cron_fields,
    )

    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    draft = parse_cron_draft(COMPLETE[1:])
    draft["name"] = "[literal] 日本 " * 12
    captured: list[str] = []
    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_cron_form(draft)
        app.input = pipe

        def rendered(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None or len(captured) == 2:
                return
            cursor = screen.get_cursor_position(app.layout.current_window)
            line = "".join(screen.data_buffer[cursor.y][x].char for x in range(columns))
            if not captured and "Check [literal]" in line:
                captured.append(line)
                names = visible_cron_fields(draft)
                pipe.send_text("\t" * (len(names) - names.index("prompt")))
            elif captured and "Create scheduled task" in line:
                captured.append(line)
                pipe.send_text("\x13")

        async def stop_if_stalled() -> None:
            await asyncio.sleep(2)
            app.exit(result=None)

        def start() -> None:
            app.create_background_task(stop_if_stalled())
            pipe.send_text("\t" * visible_cron_fields(draft).index("prompt"))

        app.after_render += rendered
        result = app.run(pre_run=start)
    assert len(captured) == 2, "Selected values and Create must stay visible and accessible"
    assert result is not None
    assert parse_cron_draft(result)["name"] == draft["name"]


def test_agent_complete_cron_remains_in_same_turn(monkeypatch: pytest.MonkeyPatch) -> None:
    ports = repl_slash_ports()
    monkeypatch.setattr(ports, "tty_interactive", lambda: True)
    commands: list[str] = []

    def dispatch(command: str, **kwargs: Any) -> bool:
        commands.append(command)
        return True

    monkeypatch.setattr(ports, "dispatch", dispatch)
    session = Session()
    context = ActionToolScope(
        session=session, console=Console(file=io.StringIO()), slash_ports=ports
    )
    assert execute_slash_tool({"command": "/cron", "args": COMPLETE}, context) is True
    assert len(commands) == 1
    assert not session.terminal.pending_prompt_autosubmit


def test_template_defaults_match_task_preparation_and_keep_supplied_values() -> None:
    from core.agent_harness import load_loop_template
    from surfaces.interactive_shell.ui.cron_input.arguments import (
        cron_add_needs_input,
        cron_template_defaults,
        parse_cron_draft,
        visible_cron_fields,
    )

    template = load_loop_template("pr-ci")
    defaults = cron_template_defaults("pr-ci")
    assert defaults == {
        "name": template.name,
        "cron_expr": template.cron,
        "mode": template.mode or "report",
    }
    args = [
        "add",
        "--kind",
        "manual_loop",
        "--template",
        "pr-ci",
        "--provider",
        "interactive_shell",
    ]
    assert cron_add_needs_input(args)
    assert not cron_add_needs_input([*args, "--owner", "example", "--repo", "app"])
    draft = parse_cron_draft(
        [*args[1:], "--name", "Custom name", "--city", "Invalid for this kind"]
    )
    assert draft["name"] == "Custom name"
    agent_fields = {"skill_name", "stateless", "branch", "pr_number"}
    assert agent_fields <= set(visible_cron_fields(draft))
    draft["mode"] = "report"
    assert not agent_fields.intersection(visible_cron_fields(draft))
    # An incompatible supplied option must remain editable, not be silently dropped.
    assert "city" in visible_cron_fields(draft)


@pytest.mark.parametrize("flag", ["--kind", "--provider"])
def test_required_click_options_without_defaults_open_the_form(flag: str) -> None:
    from surfaces.interactive_shell.ui.cron_input.arguments import (
        cron_add_needs_input,
        missing_cron_fields,
        parse_cron_draft,
    )

    args = [*COMPLETE, "--chat-id", "example-destination"]
    index = args.index(flag)
    del args[index : index + 2]
    draft = parse_cron_draft(args[1:])
    assert draft[flag[2:]] == ""
    assert flag[2:] in missing_cron_fields(draft)
    assert cron_add_needs_input(args)


def test_skill_aliases_expose_repairable_required_inputs() -> None:
    from surfaces.interactive_shell.ui.cron_input.arguments import (
        cron_add_needs_input,
        parse_cron_draft,
        validate_cron_draft,
        visible_cron_fields,
    )

    args = [
        "add",
        "--kind",
        "recurring_skill",
        "--skill",
        "github-ci-health",
        "--provider",
        "interactive_shell",
        "--cron",
        "0 9 * * *",
    ]
    assert cron_add_needs_input(args)
    draft = parse_cron_draft(args[1:])
    assert {"owner", "repo"} <= set(visible_cron_fields(draft))
    draft.update(owner="example", repo="app")
    assert validate_cron_draft(draft)
    draft["skill_name"] = "morning-report"
    assert "city" in visible_cron_fields(draft)


def test_choice_editor_preserves_case_insensitive_supplied_values() -> None:
    from prompt_toolkit.application import create_app_session
    from prompt_toolkit.output import DummyOutput

    from surfaces.interactive_shell.ui.cron_input import build_cron_form
    from surfaces.interactive_shell.ui.cron_input.arguments import (
        parse_cron_draft,
        visible_cron_fields,
    )
    from tests.interactive_shell.command_registry.test_command_inputs import run_keys

    draft = parse_cron_draft(COMPLETE[1:])
    draft.update(kind="MANUAL_LOOP", provider="INTERACTIVE_SHELL")
    with create_app_session(output=DummyOutput()):
        app = build_cron_form(draft)
        # Open and accept both existing choices without changing their selection.
        keys = "\r\r" + "\t" * visible_cron_fields(draft).index("provider") + "\r\r\x13\x03"
        result = run_keys(app, keys)
    assert result is not None
    saved = parse_cron_draft(result)
    assert saved["kind"] == "manual_loop"
    assert saved["provider"] == "interactive_shell"


def test_option_looking_text_is_preserved_like_click() -> None:
    from surfaces.interactive_shell.ui.cron_input.arguments import (
        cron_add_needs_input,
        parse_cron_draft,
        validate_cron_draft,
    )

    args = [
        "add",
        "--kind",
        "manual_loop",
        "--provider",
        "interactive_shell",
        "--prompt",
        "--summarize",
    ]
    assert cron_add_needs_input(args)
    draft = parse_cron_draft(args[1:])
    assert draft["prompt"] == "--summarize"
    draft["cron_expr"] = "0 9 * * *"
    assert parse_cron_draft(validate_cron_draft(draft))["prompt"] == "--summarize"
