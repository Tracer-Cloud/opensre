"""``surfaces.entrypoint`` is the one place that composes the CLI, the shell and the gateway entry."""

from __future__ import annotations

from unittest.mock import patch

from surfaces.cli.host import CliHost
from surfaces.entrypoint import main


def test_entrypoint_hands_the_cli_a_host_that_opens_the_shell_with_the_click_group() -> None:
    # Arrange: capture the host the entrypoint builds instead of running the CLI.
    captured: list[CliHost] = []

    def _fake_cli_main(argv: list[str] | None, *, host: CliHost) -> int:
        captured.append(host)
        return 0

    with (
        patch("surfaces.cli.app.main", _fake_cli_main),
        patch("surfaces.interactive_shell.run_repl", return_value=7) as fake_run_repl,
    ):
        # Act
        exit_code = main(["--no-interactive"])
        host = captured[0]
        assert host.launch_shell is not None
        shell_exit = host.launch_shell(_config(), "abc123", _after_banner)

    # Assert: the shell was launched with the CLI's click group for grounding
    # and the held-back launch work handed through untouched.
    from surfaces.cli.app import cli

    assert exit_code == 0
    assert shell_exit == 7
    fake_run_repl.assert_called_once()
    kwargs = fake_run_repl.call_args.kwargs
    assert kwargs["resume_session_id"] == "abc123"
    assert kwargs["capture_shell_rendered"] is False
    assert kwargs["cli_command_group"] is cli
    assert kwargs["after_banner"] is _after_banner


def _after_banner() -> None:
    """Stand-in for the launch work the CLI defers until the banner is painted."""


def test_entrypoint_hands_the_cli_a_foreground_gateway_runner() -> None:
    captured: list[CliHost] = []

    def _fake_cli_main(argv: list[str] | None, *, host: CliHost) -> int:
        captured.append(host)
        return 0

    with (
        patch("surfaces.cli.app.main", _fake_cli_main),
        patch("surfaces.gateway_entry.start_gateway") as fake_start,
    ):
        main(["gateway", "start", "--foreground"])
        host = captured[0]
        assert host.start_gateway_foreground is not None
        host.start_gateway_foreground()

    fake_start.assert_called_once_with()


def _config():
    from config.repl_config import ReplConfig

    return ReplConfig.load(cli_enabled=True)
