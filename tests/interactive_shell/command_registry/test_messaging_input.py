"""Messaging selection collects missing fields without changing direct CLI calls."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from prompt_toolkit.application import create_app_session
from prompt_toolkit.output import DummyOutput
from rich.console import Console

from config.command_inputs import needs_command_input
from config.constants.slash_commands import QUEUED_COMMAND_KEY
from core.agent_harness.tools.tool_context import ActionToolScope
from integrations import store
from surfaces.cli.commands.messaging import messaging
from surfaces.interactive_shell.command_registry import cli_parity, dispatch_slash
from surfaces.interactive_shell.runtime import input_policy
from surfaces.interactive_shell.runtime.slash_adapter import repl_slash_ports
from surfaces.interactive_shell.session import Session
from tests.interactive_shell.command_registry.test_command_inputs import run_keys
from tools.interactive_shell.actions.slash import execute_slash_tool


@pytest.fixture
def isolated_store(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    path = tmp_path / "integrations.json"
    path.write_text(json.dumps({"version": 2, "integrations": []}))
    monkeypatch.setattr(store, "STORE_PATH", path)
    return path


def test_incomplete_messaging_metadata_preserves_aliases_and_explicit_calls() -> None:
    for args in (
        ["status"],
        ["pair"],
        ["allow", "-p", "slack"],
        ["allow", "-pslack"],
        ["allow", "-uU001"],
        ["allow", "--user-id=U001"],
        ["revoke", "--platform", "slack"],
    ):
        assert needs_command_input("/messaging", args), args
    for args in (
        ["status", "-p", "slack"],
        ["allow", "--platform=slack", "-u", "U001"],
        ["status", "--help"],
        ["allow", "--platform"],
        ["allow", "--unknown", "value"],
        ["status", "--user-id", "U001"],
    ):
        assert not needs_command_input("/messaging", args), args


def test_inline_messaging_options_do_not_enable_unrelated_collectors() -> None:
    assert needs_command_input("/messaging", ["allow", "--platform=slack"])
    assert not needs_command_input("/goal", ["set", "--max-turns=3"])
    assert not needs_command_input("/work", ["add", "--project=payments"])


def test_attached_short_values_survive_form_validation() -> None:
    from surfaces.interactive_shell.ui.messaging_input import build_messaging_form

    with create_app_session(output=DummyOutput()):
        for args, keys in (
            (["allow", "-pslack"], "U001\r\x13\x03"),
            (["allow", "-uU001"], "\x1b[B\r\x13\x03"),
        ):
            app = build_messaging_form(args, allowed_users=lambda _: ())
            assert run_keys(app, keys) == ["allow", "--platform", "slack", "--user-id", "U001"]


def test_status_selector_collects_platform_through_real_dispatch(
    monkeypatch: pytest.MonkeyPatch, isolated_store: Path
) -> None:
    from surfaces.interactive_shell.command_registry import messaging_cmds

    monkeypatch.setattr(input_policy, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(messaging_cmds, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(messaging_cmds, "run_command_input", lambda app: run_keys(app, "\r\x13"))
    calls: list[list[str]] = []

    def run_cli(_console: Console, args: list[str], **_kwargs: Any) -> bool:
        calls.append(args)
        result = CliRunner().invoke(messaging, args[1:])
        assert result.exit_code == 0, result.output
        return True

    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    session = Session()
    session.terminal.exclusive_stdin_active = input_policy.turn_needs_exclusive_stdin(
        "/messaging status", session
    )
    assert session.terminal.exclusive_stdin_active
    with create_app_session(output=DummyOutput()):
        assert dispatch_slash(
            "/messaging status", session, Console(file=io.StringIO()), is_tty=True
        )
    assert calls == [["messaging", "status", "--platform", "telegram"]]
    assert json.loads(isolated_store.read_text())["integrations"] == []


def test_agent_incomplete_messaging_defers_but_complete_calls_stay_inline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ports = repl_slash_ports()
    monkeypatch.setattr(ports, "tty_interactive", lambda: True)
    calls: list[str] = []

    def dispatch(command: str, **_kwargs: Any) -> bool:
        calls.append(command)
        return True

    monkeypatch.setattr(ports, "dispatch", dispatch)
    ctx = ActionToolScope(session=Session(), console=Console(file=io.StringIO()), slash_ports=ports)
    result = execute_slash_tool({"command": "/messaging", "args": ["status"]}, ctx)
    assert isinstance(result, dict) and QUEUED_COMMAND_KEY in result
    assert not calls
    execute_slash_tool({"command": "/messaging", "args": ["status", "-p", "slack"]}, ctx)
    assert calls == ["/messaging status -p slack"]


@pytest.mark.parametrize("via_help", [False, True])
def test_root_and_help_selection_continue_into_platform_input(
    monkeypatch: pytest.MonkeyPatch, via_help: bool
) -> None:
    from surfaces.interactive_shell.command_registry import help as help_commands
    from surfaces.interactive_shell.command_registry import messaging_cmds

    monkeypatch.setattr(messaging_cmds, "repl_tty_interactive", lambda: True)
    monkeypatch.setattr(messaging_cmds, "repl_choose_subcommand", lambda **_: "status")
    monkeypatch.setattr(messaging_cmds, "run_command_input", lambda app: run_keys(app, "\r\x13"))
    calls: list[list[str]] = []

    def run_cli(_console: Console, args: list[str], **_kwargs: Any) -> bool:
        calls.append(args)
        return True

    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    command = "/messaging"
    if via_help:
        monkeypatch.setattr(help_commands, "repl_tty_interactive", lambda: True)
        monkeypatch.setattr(help_commands, "browse_help_commands", lambda _: "/messaging")
        monkeypatch.setattr(help_commands, "repl_choose_subcommand", lambda **_: "status")
        command = "/help"
    session = Session()
    session.terminal.exclusive_stdin_active = True
    with create_app_session(output=DummyOutput()):
        assert dispatch_slash(command, session, Console(file=io.StringIO()), is_tty=True)
    assert calls == [["messaging", "status", "--platform", "telegram"]]


def test_allow_form_keeps_platform_and_invalid_draft_until_corrected(isolated_store: Path) -> None:
    from surfaces.interactive_shell.ui.messaging_input import build_messaging_form

    with create_app_session(output=DummyOutput()):
        app = build_messaging_form(["allow", "-p", "telegram"], allowed_users=lambda _: ())
        result = run_keys(app, "@wrong\r\x13\x01\x0b12345\r\x13")
    assert result == ["allow", "--platform", "telegram", "--user-id", "12345"]
    assert json.loads(isolated_store.read_text())["integrations"] == []
    outcome = CliRunner().invoke(messaging, result)
    assert outcome.exit_code == 0, outcome.output
    assert store.get_integration("telegram")["credentials"]["identity_policy"][
        "allowed_user_ids"
    ] == ["12345"]


def test_revoke_search_returns_exact_user_only_after_review(isolated_store: Path) -> None:
    from surfaces.interactive_shell.ui.messaging_input import build_messaging_form

    users = ("U001", "U002")
    with create_app_session(output=DummyOutput()):
        app = build_messaging_form(["revoke", "-p", "slack"], allowed_users=lambda _: users)
        assert run_keys(app, "U002\r\x03") is None
        app = build_messaging_form(["revoke", "-p", "slack"], allowed_users=lambda _: users)
        result = run_keys(app, "U002\r\x13")
    assert result == ["revoke", "--platform", "slack", "--user-id", "U002"]
    assert json.loads(isolated_store.read_text())["integrations"] == []


@pytest.mark.parametrize("columns,rows", [(40, 12), (100, 30), (160, 40)])
def test_revoke_review_keeps_actions_reachable_and_returns_full_id(columns: int, rows: int) -> None:
    import asyncio

    from prompt_toolkit.data_structures import Size
    from prompt_toolkit.input import create_pipe_input

    from surfaces.interactive_shell.ui.messaging_input import build_messaging_form

    user_id = "abcdef0123456789" * 4
    captured: list[str] = []

    class SizedOutput(DummyOutput):
        def get_size(self) -> Size:
            return Size(rows=rows, columns=columns)

    with create_pipe_input() as pipe, create_app_session(output=SizedOutput()):
        app = build_messaging_form(["revoke", "-p", "buzz"], allowed_users=lambda _: (user_id,))
        app.input = pipe

        def rendered(_app: Any) -> None:
            screen = app.renderer.last_rendered_screen
            if screen is None:
                return
            text = "\n".join(
                "".join(screen.data_buffer[y][x].char for x in range(columns)) for y in range(rows)
            )
            if not captured:
                captured.append(text)
                pipe.send_text("\r")
            elif len(captured) == 1 and "Revoke access" in text and "Cancel" in text:
                captured.append(text)
                pipe.send_text("\x13")

        async def stop_if_stalled() -> None:
            await asyncio.sleep(2)
            app.exit(result=None)

        app.after_render += rendered
        result = app.run(pre_run=lambda: app.create_background_task(stop_if_stalled()))
    assert len(captured) == 2, "The review must keep Revoke access and Cancel visible"
    assert result == ["revoke", "--platform", "buzz", "--user-id", user_id]


def test_cancelled_form_and_empty_revoke_never_execute(
    monkeypatch: pytest.MonkeyPatch, isolated_store: Path
) -> None:
    from surfaces.interactive_shell.command_registry import messaging_cmds

    def unexpected(*_args: Any, **_kwargs: Any) -> bool:
        raise AssertionError("Cancelled or empty selection must not execute")

    monkeypatch.setattr(messaging_cmds, "repl_tty_interactive", lambda: True)
    session = Session()
    session.terminal.exclusive_stdin_active = True
    with create_app_session(output=DummyOutput()):
        for args, keys in ((["pair"], "\r\x03"), (["revoke", "-p", "slack"], "\r\x13\x03")):
            monkeypatch.setattr(
                messaging_cmds, "run_command_input", lambda app, keys=keys: run_keys(app, keys)
            )
            assert messaging_cmds.cmd_messaging(
                session, Console(file=io.StringIO()), args, run_cli=unexpected
            )
    monkeypatch.setattr(messaging_cmds, "repl_choose_subcommand", lambda **_: None)
    assert messaging_cmds.cmd_messaging(
        session, Console(file=io.StringIO()), [], run_cli=unexpected
    )
    assert json.loads(isolated_store.read_text())["integrations"] == []


@pytest.mark.parametrize(
    "mode",
    ["complete", "help", "malformed", "uppercase", "headless", "non-tty", "override", "no-stdin"],
)
def test_ineligible_calls_bypass_input_unchanged(
    monkeypatch: pytest.MonkeyPatch, mode: str
) -> None:
    from contextlib import nullcontext

    from config.interactive_override import forced_non_interactive
    from surfaces.interactive_shell.command_registry import messaging_cmds

    def unexpected(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("Direct calls must not open input")

    monkeypatch.setattr(messaging_cmds, "run_command_input", unexpected)
    monkeypatch.setattr(messaging_cmds, "repl_tty_interactive", lambda: mode != "non-tty")
    session = Session()
    session.terminal.exclusive_stdin_active = mode != "no-stdin"
    if mode == "headless":
        session.terminal = None
    args = {
        "complete": ["allow", "-p", "slack", "-u", "U001"],
        "help": ["status", "--help"],
        "malformed": ["status", "--unknown"],
        "uppercase": ["STATUS"],
    }.get(mode, ["status"])
    calls: list[list[str]] = []

    def run_cli(_console: Console, argv: list[str], **_kwargs: Any) -> bool:
        calls.append(argv)
        return True

    with forced_non_interactive() if mode == "override" else nullcontext():
        assert messaging_cmds.cmd_messaging(
            session, Console(file=io.StringIO()), args, run_cli=run_cli
        )
    assert calls == [["messaging", *args]]
