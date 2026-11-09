"""Login menus render before delegating credential setup to the CLI."""

from unittest.mock import ANY, Mock

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import cli_parity, login_cmds
from surfaces.interactive_shell.session import Session


@pytest.mark.parametrize("command", ["login", "auth"])
@pytest.mark.parametrize("selection", ["chatgpt", None])
def test_login_selects_locally_before_starting_cli(monkeypatch, command, selection) -> None:
    from surfaces.shared.llm_setup.auth_profiles import resolve_auth_profile

    session = Session()
    run_cli = Mock(return_value=True)

    def select_provider():
        run_cli.assert_not_called()
        return resolve_auth_profile(selection) if selection else None

    monkeypatch.setattr(login_cmds, "session_terminal", lambda _: object())
    monkeypatch.setattr(login_cmds, "prompt_login_action", lambda: "connect")
    monkeypatch.setattr(login_cmds, "prompt_auth_provider", select_provider)
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    handler = cli_parity._cmd_login if command == "login" else cli_parity._cmd_auth
    assert handler(session, Console(), [] if command == "login" else ["login"])
    if selection:
        run_cli.assert_called_once_with(
            ANY,
            ["login", "connect", selection] if command == "login" else ["auth", "login", selection],
            capture_output=False,
            session=session,
        )
    else:
        run_cli.assert_not_called()


@pytest.mark.parametrize("headless,args", [(True, []), (False, ["chatgpt"]), (False, ["--help"])])
def test_login_preserves_direct_and_headless_delegation(monkeypatch, headless, args) -> None:
    session = Session()
    run_cli = Mock(return_value=True)
    monkeypatch.setattr(login_cmds, "session_terminal", lambda _: None if headless else object())
    monkeypatch.setattr(login_cmds, "prompt_auth_provider", Mock(side_effect=AssertionError))
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    assert cli_parity._cmd_login(session, Console(), args)
    assert run_cli.call_args.args[1] == ["login", *args]


@pytest.mark.parametrize("action", ["list", "verify", "disconnect", None])
def test_connection_actions_and_cancellation_delegate_only_the_selected_operation(
    monkeypatch, action
) -> None:
    session = Session()
    run_cli = Mock(return_value=True)
    monkeypatch.setattr(login_cmds, "session_terminal", lambda _: object())
    monkeypatch.setattr(login_cmds, "prompt_login_action", lambda: action)
    monkeypatch.setattr(login_cmds, "prompt_connection_target", lambda _: "openai")
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    assert cli_parity._cmd_login(session, Console(), [])
    if action is None:
        run_cli.assert_not_called()
    else:
        args = ["login", action, *(["openai"] if action != "list" else [])]
        run_cli.assert_called_once_with(ANY, args, capture_output=True, session=session)


def test_canonical_discovery_hides_aliases_but_explicit_alias_help_still_works() -> None:
    from io import StringIO

    from prompt_toolkit.completion import CompleteEvent
    from prompt_toolkit.document import Document

    from surfaces.interactive_shell.command_registry.help import _cmd_help, _help_sections
    from surfaces.interactive_shell.ui.input_prompt.completion import ShellCompleter

    visible = {command.name for _, commands in _help_sections() for command in commands}
    completions = {
        row.text for row in ShellCompleter().get_completions(Document("/"), CompleteEvent())
    }
    assert {"/login", "/model", "/account"} <= visible & completions
    assert not {"/auth", "/logout"} & (visible | completions)
    output = StringIO()
    assert _cmd_help(Session(), Console(file=output), ["/auth"])
    assert "Compatibility" in output.getvalue()
