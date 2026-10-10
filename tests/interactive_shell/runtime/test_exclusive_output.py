"""Agent-selected cron lists yield stdin without dropping the remaining actions."""

from __future__ import annotations

import asyncio
import io

import pytest
from prompt_toolkit.application import Application
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.constants.slash_commands import QUEUED_COMMAND_KEY
from core.agent_harness.tools import ActionToolScope
from surfaces.interactive_shell.command_registry import cron_cmds
from surfaces.interactive_shell.runtime import slash_adapter
from surfaces.interactive_shell.session import Session
from tools.interactive_shell.actions.slash import execute_slash_tool


@pytest.mark.asyncio
async def test_cron_list_returns_ids_to_the_same_agent_turn_with_prompt_suspended(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = Session()
    session.terminal.main_loop = asyncio.get_running_loop()
    observed: list[tuple[bool, bool]] = []
    removed: list[list[str]] = []

    def _print_schedules(console: Console, _loops: object) -> None:
        observed.append((session.terminal.exclusive_stdin_active, app._running_in_terminal))
        console.print("Daily audit · ID: task-123456789")

    def _remove(_console: Console, args: list[str], **_kwargs: object) -> bool:
        removed.append(args)
        return True

    monkeypatch.setattr(slash_adapter, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(cron_cmds, "list_loop_summaries", lambda: [object()])
    monkeypatch.setattr(cron_cmds, "print_loop_schedules", _print_schedules)
    monkeypatch.setattr(
        "surfaces.interactive_shell.command_registry.cli_parity.run_cli_command", _remove
    )
    with create_pipe_input() as input_stream:
        started = asyncio.Event()
        app: Application[None] = Application(input=input_stream, output=DummyOutput())
        session.terminal.prompt_app = app
        prompt = asyncio.create_task(app.run_async(pre_run=started.set))
        await started.wait()
        try:
            ctx = ActionToolScope(
                session=session,
                console=Console(file=io.StringIO()),
                is_tty=True,
                slash_ports=slash_adapter.repl_slash_ports(),
                turn_user_message="Remove my daily audit schedule",
            )
            result = await asyncio.to_thread(
                execute_slash_tool, {"command": "/cron", "args": ["list"]}, ctx
            )
            assert isinstance(result, dict)
            assert "task-123456789" in result["output"]
            assert QUEUED_COMMAND_KEY not in result
            assert observed == [(True, True)]
            assert session.terminal.exclusive_stdin_active is False
            assert session.terminal.pending_prompt_default is None
            assert app.is_running and not app._running_in_terminal
            await asyncio.to_thread(
                execute_slash_tool,
                {"command": "/cron", "args": ["remove", "task-123456789"]},
                ctx,
            )
            assert removed == [["cron", "remove", "task-123456789"]]
        finally:
            app.exit(result=None)
            await prompt
