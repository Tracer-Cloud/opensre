"""Login menus render before delegating credential setup to the CLI."""

from unittest.mock import ANY, Mock

import pytest
from rich.console import Console

from surfaces.interactive_shell.command_registry import cli_parity
from surfaces.interactive_shell.session import Session


@pytest.mark.parametrize("command", ["login", "auth"])
@pytest.mark.parametrize("selection", ["chatgpt", None])
def test_login_selects_locally_before_starting_cli(monkeypatch, command, selection) -> None:
    from surfaces.shared.llm_setup import auth_menu

    session = Session()
    run_cli = Mock(return_value=True)

    def select_provider():
        run_cli.assert_not_called()
        return auth_menu.resolve_auth_profile(selection) if selection else None

    monkeypatch.setattr(cli_parity, "session_terminal", lambda _: object())
    monkeypatch.setattr(auth_menu, "prompt_auth_provider", select_provider)
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    handler = cli_parity._cmd_login if command == "login" else cli_parity._cmd_auth
    assert handler(session, Console(), [] if command == "login" else ["login"])
    if selection:
        run_cli.assert_called_once_with(
            ANY,
            ["auth", "login", selection],
            capture_output=False,
            session=session,
        )
    else:
        run_cli.assert_not_called()


@pytest.mark.parametrize("headless,args", [(True, []), (False, ["chatgpt"]), (False, ["--help"])])
def test_login_preserves_direct_and_headless_delegation(monkeypatch, headless, args) -> None:
    from surfaces.shared.llm_setup import auth_menu

    session = Session()
    run_cli = Mock(return_value=True)
    monkeypatch.setattr(cli_parity, "session_terminal", lambda _: None if headless else object())
    monkeypatch.setattr(auth_menu, "prompt_auth_provider", Mock(side_effect=AssertionError))
    monkeypatch.setattr(cli_parity, "run_cli_command", run_cli)
    assert cli_parity._cmd_login(session, Console(), args)
    assert run_cli.call_args.args[1] == ["auth", "login", *args]
