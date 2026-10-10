"""CLI group discovery reaches real commands without losing cron records."""

from __future__ import annotations

import io
from dataclasses import replace
from unittest.mock import Mock

import click
import pytest
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document
from rich.cells import cell_len
from rich.console import Console

from config.cli_command_choices import CLI_COMMAND_CHOICES
from infrastructure.scheduling.scheduler.loops import LoopSummary
from infrastructure.scheduling.scheduler.types import Provider, TaskKind
from surfaces.cli.commands.command_specs import COMMAND_SPECS_BY_NAME, load_command
from surfaces.interactive_shell.command_registry import SLASH_COMMANDS, cron_cmds
from surfaces.interactive_shell.session import Session
from surfaces.interactive_shell.ui.input_prompt.completion import ShellCompleter
from surfaces.interactive_shell.ui.input_prompt.key_bindings import (
    _open_exact_command_subcommand_tray,
)


def test_discovery_matches_real_click_groups_at_every_registered_depth() -> None:
    for path, choices in CLI_COMMAND_CHOICES.items():
        command = load_command(COMMAND_SPECS_BY_NAME[path[0][1:]])
        for part in path[1:]:
            assert isinstance(command, click.Group)
            command = command.commands[part]
        assert isinstance(command, click.Group)
        names = {name for name, child in command.commands.items() if not child.hidden}
        assert {name for name, _ in choices} == names, path
        if len(path) == 1:
            assert SLASH_COMMANDS[path[0]].first_arg_completions == choices
        buffer = Buffer(document=Document(" ".join(path)))
        assert _open_exact_command_subcommand_tray(buffer)
        assert buffer.complete_state is not None
        assert {c.text for c in buffer.complete_state.completions} == names


def test_nested_completion_filters_and_opens_the_next_group() -> None:
    completer = ShellCompleter()
    event = CompleteEvent(completion_requested=True)
    names = [c.text for c in completer.get_completions(Document("/sentry digest sch"), event)]
    assert names == ["schedule"]
    names = [c.text for c in completer.get_completions(Document("/sentry digest schedule "), event)]
    assert names == ["list", "add", "run", "remove"]
    # An already supplied value/flag must not be replaced by group choices.
    assert not list(completer.get_completions(Document("/cron logs task-id "), event))


@pytest.mark.parametrize("width", [40, 100, 160])
def test_cron_native_list_keeps_all_records_at_each_width(
    monkeypatch: pytest.MonkeyPatch, width: int
) -> None:
    from surfaces.shared.terminal.tables import schedule_listing

    loop = LoopSummary(
        id="task",
        task_ids=("task",),
        name="Audit",
        description="Check deployment",
        prompt="",
        kind=TaskKind.MANUAL_LOOP,
        cron="0 8 * * *",
        timezone="UTC",
        provider=Provider.INTERACTIVE_SHELL,
        chat_id="",
        channels=(),
        enabled=True,
        window_hours=24,
        last_run=None,
        next_run=None,
    )
    loops = [
        replace(loop, id=f"task-{i}-123456789", name=f"[literal] Audit 日本 {i}") for i in range(8)
    ]
    monkeypatch.setattr(cron_cmds, "list_loop_summaries", lambda: loops)
    monkeypatch.setattr(schedule_listing, "latest_loop_runs", lambda _: {})
    output = io.StringIO()
    run_cli = Mock(side_effect=AssertionError("Local list must not capture subprocess output"))
    cron_cmds.cmd_cron(
        Session(), Console(file=output, width=width, height=25), ["list"], run_cli=run_cli
    )
    text = output.getvalue()
    for loop in loops:
        assert loop.id in text
    assert "[literal]" in text and "UTC" in text
    assert "Ctrl+O" not in text
    assert max(cell_len(line) for line in text.splitlines()) <= width


def test_cron_cancel_and_noninteractive_guidance_do_not_run_tasks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(cron_cmds, "repl_choose_subcommand", lambda **_: None)
    run_cli = Mock(return_value=True)
    console = Console(file=io.StringIO())
    session = Session()
    assert cron_cmds.cmd_cron(session, console, [], run_cli=run_cli)
    run_cli.assert_not_called()
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: False)
    assert cron_cmds.cmd_cron(session, console, [], run_cli=run_cli)
    assert run_cli.call_args.args[1] == ["cron", "--help"]


def test_help_browser_reaches_nested_leaf_and_can_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    from surfaces.interactive_shell.command_registry import help as help_commands

    choices = iter(["digest", "schedule", "list"])
    monkeypatch.setattr(help_commands, "repl_choose_subcommand", lambda **_: next(choices))
    assert help_commands._command_text(SLASH_COMMANDS["/sentry"]) == "/sentry digest schedule list"
    choices = iter(["digest", None])
    assert help_commands._command_text(SLASH_COMMANDS["/sentry"]) is None


def test_cron_picker_selection_and_headless_delegation(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(cron_cmds, "repl_choose_subcommand", lambda **_: "status")
    run_cli = Mock(return_value=True)
    console = Console(file=io.StringIO())
    assert cron_cmds.cmd_cron(Session(), console, [], run_cli=run_cli)
    assert run_cli.call_args.args[1] == ["cron", "status"]
    session = Session()
    session.terminal = None
    assert cron_cmds.cmd_cron(session, console, ["list"], run_cli=run_cli)
    assert run_cli.call_args.args[1] == ["cron", "list"]


@pytest.mark.parametrize("selected", ["logs", "remove", "run"])
def test_incomplete_cron_choices_return_to_editable_composer(
    monkeypatch: pytest.MonkeyPatch, selected: str
) -> None:
    monkeypatch.setattr(cron_cmds, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(cron_cmds, "repl_choose_subcommand", lambda **_: selected)
    run_cli = Mock(side_effect=AssertionError("Incomplete choices must not execute"))
    session = Session()
    session.terminal.pending_prompt_autosubmit = True
    assert cron_cmds.cmd_cron(session, Console(file=io.StringIO()), [], run_cli=run_cli)
    assert session.terminal.pending_prompt_default == f"/cron {selected} "
    assert not session.terminal.pending_prompt_autosubmit
